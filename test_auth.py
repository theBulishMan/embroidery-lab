import http.client
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from studio_auth import AuthStore, AuthError, digest


class AuthStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = AuthStore(Path(self.temp.name) / 'accounts.sqlite')
        self.admin = self.store.bootstrap('design', 'a-strong-admin-password', ['old-art'])

    def tearDown(self):
        self.temp.cleanup()

    def register(self, username='member', code=None, ip='127.0.0.2'):
        if code is None:
            code = self.store.create_invite(self.admin, {'max_uses': 1}, 'local')['code']
        return self.store.authenticate({'action': 'register', 'username': username, 'password': 'member-password-123', 'invite_code': code, 'role': 'admin'}, ip)

    def test_register_requires_invite_and_never_escalates_role(self):
        for code in ('', 'FAKE-CODE-0000000'):
            with self.assertRaises(AuthError):
                self.register(code=code)
        user, token = self.register()
        self.assertEqual(user['role'], 'user')
        self.assertEqual(self.store.current_user(token)['id'], user['id'])
        self.assertNotIn('salt', user)
        self.assertNotIn('password_hash', user)

    def test_project_ownership_is_enforced_even_for_admin(self):
        user, _ = self.register()
        self.assertEqual(self.store.project_ids(self.admin), {'old-art'})
        self.assertEqual(self.store.project_ids(user), set())
        self.store.claim_project('new-art', user)
        for project, who in [('old-art', user), ('new-art', self.admin)]:
            with self.assertRaises(AuthError) as e:
                self.store.require_project(project, who)
            self.assertEqual(e.exception.status, 404)

    def test_one_use_invite_is_atomic(self):
        code = self.store.create_invite(self.admin, {'max_uses': 1}, 'local')['code']
        def attempt(index):
            try:
                return self.register('member' + str(index), code, 'local' + str(index))[0]
            except AuthError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, range(2)))
        self.assertEqual(sum(r is not None for r in results), 1)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT uses FROM invites').fetchone()[0], 1)

    def test_duplicate_username_does_not_consume_invite(self):
        self.register()
        invite = self.store.create_invite(self.admin, {}, 'local')
        with self.assertRaises(AuthError):
            self.register(code=invite['code'])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT uses FROM invites WHERE id=?', (invite['id'],)).fetchone()[0], 0)

    def test_revocation_expiry_and_admin_gate(self):
        user, _ = self.register()
        with self.assertRaises(AuthError):
            self.store.create_invite(user, {}, 'local')
        for expired in (False, True):
            invite = self.store.create_invite(self.admin, {}, 'local')
            if expired:
                with self.store.connect() as db:
                    db.execute('UPDATE invites SET expires_at=0 WHERE id=?', (invite['id'],))
            else:
                self.store.admin_action(self.admin, {'action': 'revoke_invite', 'id': invite['id']}, 'local')
            with self.assertRaises(AuthError):
                self.register('another', invite['code'])

    def test_password_change_and_disable_revoke_sessions(self):
        user, token = self.register()
        new = self.store.change_password(user, {'current_password': 'member-password-123', 'new_password': 'changed-password-123'}, 'local')
        self.assertIsNone(self.store.current_user(token))
        self.assertIsNotNone(self.store.current_user(new))
        with self.assertRaises(AuthError):
            self.store.authenticate({'action': 'login', 'username': 'member', 'password': 'member-password-123'}, 'local')
        self.store.admin_action(self.admin, {'action': 'set_user_disabled', 'user_id': user['id'], 'disabled': True}, 'local')
        self.assertIsNone(self.store.current_user(new))
        with self.assertRaises(AuthError):
            self.store.authenticate({'action': 'login', 'username': 'member', 'password': 'changed-password-123'}, 'local')

    def test_session_expiration_logout_and_secret_storage(self):
        user, token = self.register()
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM users WHERE id=?', (user['id'],)).fetchone()
            self.assertNotEqual(row['password_hash'], 'member-password-123')
            self.assertIsNone(db.execute('SELECT 1 FROM sessions WHERE token_hash=?', (token,)).fetchone())
            db.execute('UPDATE sessions SET expires_at=0 WHERE token_hash=?', (digest(token),))
        self.assertIsNone(self.store.current_user(token))
        _, token = self.store.authenticate({'action': 'login', 'username': 'member', 'password': 'member-password-123'}, 'local')
        self.store.logout(token)
        self.assertIsNone(self.store.current_user(token))
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)

    def test_rate_limit_and_bootstrap_cannot_reset_existing_account(self):
        for _ in range(12):
            self.store.throttle('remote', 'unknown')
        with self.assertRaises(AuthError) as e:
            self.store.throttle('remote', 'unknown')
        self.assertEqual(e.exception.status, 429)
        with self.assertRaises(AuthError):
            self.store.bootstrap('design', 'do-not-reset-password')


class HTTPIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import studio_server as server
        cls.module = server
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        cls.store = AuthStore(root / 'accounts.sqlite')
        cls.admin = cls.store.bootstrap('design', 'admin-password-123', ['private-art'])
        invite = cls.store.create_invite(cls.admin, {}, 'local')
        cls.user, cls.user_token = cls.store.authenticate({'action': 'register', 'username': 'member', 'password': 'member-password-123', 'invite_code': invite['code']}, 'local')
        _, cls.admin_token = cls.store.authenticate({'action': 'login', 'username': 'design', 'password': 'admin-password-123'}, 'local')
        cls.project_root = root / 'projects'
        (cls.project_root / 'private-art').mkdir(parents=True)
        (cls.project_root / 'private-art' / 'design.dst').write_bytes(b'private-machine-file')
        cls.patches = [patch.object(server, 'AUTH_REQUIRED', True), patch.object(server, 'COOKIE_NAME', 'stitch_session'),
                       patch.dict('os.environ', {'EMBROIDERY_AUTH_DB': str(root / 'accounts.sqlite')}),
                       patch.object(server.service, 'PROJECTS', cls.project_root),
                       patch.object(server.service, 'list_projects', return_value=[{'id': 'private-art', 'title': 'Private'}]),
                       patch.object(server.service, 'read_project', return_value={'id': 'private-art'}),
                       patch.object(server.service, 'job_view', return_value={'id': 'job1', 'project_id': 'private-art'})]
        for p in cls.patches:
            p.start()
        cls.server = server.StudioHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        for p in reversed(cls.patches):
            p.stop()
        cls.temp.cleanup()

    def request(self, path, token=None, body=None, origin=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        headers = {}
        if token:
            headers['Cookie'] = 'stitch_session=' + token
        if origin:
            headers['Origin'] = origin
        if body is not None:
            headers['Content-Type'] = 'application/json'
        conn.request('GET' if body is None else 'POST', path, body=json.dumps(body) if body is not None else None, headers=headers)
        response = conn.getresponse()
        result = (response.status, dict(response.getheaders()), response.read())
        conn.close()
        return result

    def test_public_login_and_anonymous_blocked_resources(self):
        code, headers, _ = self.request('/')
        self.assertEqual((code, headers['Location']), (303, '/login'))
        self.assertEqual(self.request('/login')[0], 200)
        self.assertEqual(self.request('/login-art.svg')[0], 200)
        for route in ['/api/projects', '/api/projects/private-art', '/api/jobs/job1', '/assets/private-art/design.dst', '/admin.js', '/studio.js']:
            self.assertEqual(self.request(route)[0], 401, route)

    def test_users_cannot_get_or_change_other_projects_jobs_assets(self):
        import studio_service
        self.assertEqual(json.loads(self.request('/api/projects', self.user_token)[2]),
                         {'projects': [], 'engine_version': studio_service.ENGINE_VERSION})
        for route in ['/api/projects/private-art', '/api/jobs/job1', '/assets/private-art/design.dst']:
            self.assertEqual(self.request(route, self.user_token)[0], 404, route)
        for action in ['references', 'size-advice', 'regions', 'craft-advice', 'split-advice', 'preserve-details', 'fit-details', 'versions', 'palette', 'plans', 'sewouts']:
            self.assertEqual(self.request('/api/projects/private-art/' + action, self.user_token, {})[0], 404, action)
        self.assertEqual(self.request('/assets/private-art/design.dst', self.admin_token)[2], b'private-machine-file')

    def test_admin_protection_origin_and_cookie_flags(self):
        for path in ['/admin', '/api/admin', '/admin.js']:
            self.assertEqual(self.request(path, self.user_token)[0], 403)
        self.assertEqual(self.request('/api/admin', self.user_token, {'action': 'create_invite'})[0], 403)
        self.assertEqual(self.request('/api/auth', body={'action': 'logout'}, origin='https://evil.example')[0], 403)
        code, headers, _ = self.request('/api/auth', body={'action': 'login', 'username': 'design', 'password': 'admin-password-123'})
        self.assertEqual(code, 200)
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('SameSite=Lax', headers['Set-Cookie'])
        with patch.object(self.module, 'PUBLIC_ORIGIN', 'https://design.pikaprint.store'):
            _, headers, _ = self.request('/api/auth', body={'action': 'login', 'username': 'design', 'password': 'admin-password-123'})
            self.assertIn('Secure', headers['Set-Cookie'])

    def test_new_project_is_claimed_before_response(self):
        def create(body, owner_callback=None):
            owner_callback('created-art')
            return {'id': 'created-job', 'project_id': 'created-art'}
        with patch.object(self.module.service, 'new_project', side_effect=create):
            self.assertEqual(self.request('/api/projects', self.user_token, {'mode': 'flat'})[0], 202)
        self.assertIn('created-art', self.store.project_ids(self.user))
        self.assertNotIn('created-art', self.store.project_ids(self.admin))


if __name__ == '__main__':
    unittest.main()

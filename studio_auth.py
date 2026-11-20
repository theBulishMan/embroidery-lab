"""Small, persistent account store. Project access is always checked server-side."""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
import time

SESSION_SECONDS = 7 * 86400
PASSWORD_ITERATIONS = 600_000
HASH_SLOTS = threading.BoundedSemaphore(2)
USERNAME = re.compile(r'^[a-z0-9_@.\-]{3,60}$')


class AuthError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password, salt=None):
    if not HASH_SLOTS.acquire(blocking=False):
        raise AuthError('登录请求较多，请稍后再试。', 429)
    try:
        salt = salt or secrets.token_hex(24)
        value = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), PASSWORD_ITERATIONS)
        return salt, value.hex()
    finally:
        HASH_SLOTS.release()


def checked_credentials(username, password):
    username = str(username or '').strip().lower()
    if not USERNAME.fullmatch(username):
        raise AuthError('账号需为 3–60 位英文、数字或邮箱，可使用 _ . -。')
    if not isinstance(password, str) or not 10 <= len(password) <= 128:
        raise AuthError('密码请使用 10–128 个字符。')
    return username, password


def public_user(row):
    return {k: row[k] for k in ('id', 'username', 'display_name', 'role')} if row else None


class AuthStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Create with restrictive permissions before SQLite opens the file.
        self.path.touch(mode=0o600, exist_ok=True)
        self.path.chmod(0o600)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user',
                    salt TEXT NOT NULL, password_hash TEXT NOT NULL,
                    disabled INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL,
                    last_login INTEGER, last_ip TEXT);
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    expires_at INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
                CREATE TABLE IF NOT EXISTS invites (
                    id TEXT PRIMARY KEY, code_hash TEXT NOT NULL UNIQUE,
                    hint TEXT NOT NULL, label TEXT NOT NULL, max_uses INTEGER NOT NULL,
                    uses INTEGER NOT NULL DEFAULT 0, expires_at INTEGER NOT NULL,
                    revoked INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL,
                    created_by TEXT NOT NULL REFERENCES users(id));
                CREATE TABLE IF NOT EXISTS project_owners (
                    project_id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id));
                CREATE INDEX IF NOT EXISTS owner_user ON project_owners(user_id);
                CREATE TABLE IF NOT EXISTS attempts (
                    key TEXT PRIMARY KEY, count INTEGER NOT NULL, reset_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, user_id TEXT, action TEXT NOT NULL,
                    ip TEXT NOT NULL, detail TEXT NOT NULL, created_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS migrations (name TEXT PRIMARY KEY);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def _event(self, db, user_id, action, ip, detail=''):
        db.execute('INSERT INTO events(user_id,action,ip,detail,created_at) VALUES(?,?,?,?,?)',
                   (user_id, action, str(ip)[:80], str(detail)[:180], int(time.time())))
        db.execute('DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 2000)')

    def initialized(self):
        with self.connect() as db:
            return bool(db.execute("SELECT 1 FROM users WHERE role='admin' AND disabled=0").fetchone())

    def bootstrap(self, username, password, project_ids=()):
        username, password = checked_credentials(username, password)
        salt, hashed = password_hash(password)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
                raise AuthError('账号库已经初始化，不会覆盖已有密码。')
            uid = secrets.token_hex(16)
            db.execute("INSERT INTO users(id,username,display_name,role,salt,password_hash,created_at) VALUES(?,?,?,'admin',?,?,?)",
                       (uid, username, '管理员', salt, hashed, int(time.time())))
            db.executemany('INSERT INTO project_owners(project_id,user_id) VALUES(?,?)', ((p, uid) for p in project_ids))
            db.execute("INSERT INTO migrations(name) VALUES('legacy-projects-assigned')")
            return public_user(db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone())

    def throttle(self, ip, username):
        now = int(time.time())
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM attempts WHERE reset_at<=?', (now,))
            for key, maximum in ((digest('ip:' + ip), 40), (digest('account:' + username), 12)):
                row = db.execute('SELECT count FROM attempts WHERE key=?', (key,)).fetchone()
                if row and row['count'] >= maximum:
                    raise AuthError('尝试次数较多，请 15 分钟后再试。', 429)
            for key in (digest('ip:' + ip), digest('account:' + username)):
                db.execute('INSERT INTO attempts(key,count,reset_at) VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET count=count+1', (key, now + 900))

    def _session(self, db, uid):
        now = int(time.time())
        db.execute('DELETE FROM sessions WHERE expires_at<=?', (now,))
        token = secrets.token_urlsafe(32)
        db.execute('INSERT INTO sessions VALUES(?,?,?)', (digest(token), uid, now + SESSION_SECONDS))
        return token

    def authenticate(self, body, ip):
        action = body.get('action')
        if action not in ('login', 'register'):
            raise AuthError('未找到登录操作。')
        raw_username = str(body.get('username', '')).strip().lower()[:60]
        self.throttle(ip, raw_username)
        username, password = checked_credentials(body.get('username'), body.get('password'))
        with self.connect() as db:
            row = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        if action == 'login':
            _, calculated = password_hash(password, row['salt'] if row else '00' * 24)
            if not row or not hmac.compare_digest(calculated, row['password_hash']) or row['disabled']:
                raise AuthError('账号或密码不正确，或账号已停用。', 401)
            with self.connect() as db:
                # Recheck state and hash in the same transaction as session creation.
                db.execute('BEGIN IMMEDIATE')
                current = db.execute('SELECT * FROM users WHERE id=? AND disabled=0', (row['id'],)).fetchone()
                if not current or current['password_hash'] != calculated:
                    raise AuthError('账号状态已更新，请重新登录。', 401)
                uid = row['id']
                token = self._session(db, uid)
                db.execute('UPDATE users SET last_login=?,last_ip=? WHERE id=?', (int(time.time()), ip, uid))
                db.execute('DELETE FROM attempts WHERE key=?', (digest('account:' + username),))
                self._event(db, uid, 'login', ip)
                return public_user(current), token
        code = str(body.get('invite_code', '')).strip().upper()
        if not 8 <= len(code) <= 100:
            raise AuthError('请填写有效邀请码，向管理员获取后再注册。', 403)
        with self.connect() as db:
            invite = db.execute('SELECT * FROM invites WHERE code_hash=?', (digest(code),)).fetchone()
        now = int(time.time())
        if not invite or invite['revoked'] or invite['uses'] >= invite['max_uses'] or invite['expires_at'] <= now:
            raise AuthError('邀请码无效、已用完或已过期，请向管理员获取新码。', 403)
        salt, hashed = password_hash(password)
        display = str(body.get('display_name', '')).strip()[:30] or username
        uid = secrets.token_hex(16)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            changed = db.execute('UPDATE invites SET uses=uses+1 WHERE id=? AND revoked=0 AND uses<max_uses AND expires_at>?', (invite['id'], int(time.time()))).rowcount
            if changed != 1:
                raise AuthError('邀请码已失效，请向管理员获取新码。', 403)
            try:
                db.execute('INSERT INTO users(id,username,display_name,salt,password_hash,created_at,last_login,last_ip) VALUES(?,?,?,?,?,?,?,?)',
                           (uid, username, display, salt, hashed, now, now, ip))
            except sqlite3.IntegrityError:
                raise AuthError('这个账号已被使用，请换一个账号。') from None
            token = self._session(db, uid)
            db.execute('DELETE FROM attempts WHERE key=?', (digest('account:' + username),))
            self._event(db, uid, 'register', ip, '邀请码 ' + invite['hint'])
            return public_user(db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()), token

    def current_user(self, token):
        if not token or len(token) > 128:
            return None
        with self.connect() as db:
            return public_user(db.execute('SELECT u.* FROM users u JOIN sessions s ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>? AND u.disabled=0',
                                          (digest(token), int(time.time()))).fetchone())

    def logout(self, token):
        with self.connect() as db:
            db.execute('DELETE FROM sessions WHERE token_hash=?', (digest(token),))

    def change_password(self, user, body, ip):
        self.throttle(ip, user['username'])
        _, new_password = checked_credentials(user['username'], body.get('new_password'))
        old = body.get('current_password')
        if not isinstance(old, str) or not 1 <= len(old) <= 128:
            raise AuthError('请输入当前密码。')
        with self.connect() as db:
            row = db.execute('SELECT * FROM users WHERE id=?', (user['id'],)).fetchone()
        _, calculated = password_hash(old, row['salt'])
        if not hmac.compare_digest(calculated, row['password_hash']):
            raise AuthError('当前密码不正确。', 403)
        salt, hashed = password_hash(new_password)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            changed = db.execute('UPDATE users SET salt=?,password_hash=? WHERE id=? AND password_hash=? AND disabled=0', (salt, hashed, user['id'], calculated)).rowcount
            if not changed:
                raise AuthError('账号状态已更新，请重新登录。', 401)
            db.execute('DELETE FROM sessions WHERE user_id=?', (user['id'],))
            token = self._session(db, user['id'])
            self._event(db, user['id'], 'password_changed', ip)
            return token

    def claim_project(self, pid, user):
        with self.connect() as db:
            db.execute('INSERT INTO project_owners(project_id,user_id) VALUES(?,?)', (pid, user['id']))

    def project_ids(self, user):
        with self.connect() as db:
            return {r['project_id'] for r in db.execute('SELECT project_id FROM project_owners WHERE user_id=?', (user['id'],))}

    def require_project(self, pid, user):
        with self.connect() as db:
            found = db.execute('SELECT 1 FROM project_owners WHERE project_id=? AND user_id=?', (pid, user['id'])).fetchone()
        if not found:
            raise AuthError('未找到作品或文件。', 404)

    def require_admin(self, user):
        if not user or user.get('role') != 'admin':
            raise AuthError('此操作需要管理员账号。', 403)

    def admin_view(self, user):
        self.require_admin(user)
        with self.connect() as db:
            users = [dict(r) for r in db.execute('SELECT id,username,display_name,role,disabled,created_at,last_login,last_ip,(SELECT COUNT(*) FROM project_owners p WHERE p.user_id=users.id) AS project_count FROM users ORDER BY created_at DESC LIMIT 500')]
            invites = [dict(r) for r in db.execute('SELECT id,hint,label,max_uses,uses,expires_at,revoked,created_at FROM invites ORDER BY created_at DESC LIMIT 100')]
            events = [dict(r) for r in db.execute('SELECT e.action,e.ip,e.detail,e.created_at,u.username FROM events e LEFT JOIN users u ON u.id=e.user_id ORDER BY e.id DESC LIMIT 50')]
        return {'users': users, 'invites': invites, 'events': events}

    def create_invite(self, user, body, ip):
        self.require_admin(user)
        maximum, days = body.get('max_uses', 1), body.get('days', 30)
        if type(maximum) is not int or not 1 <= maximum <= 1000 or type(days) is not int or not 1 <= days <= 365:
            raise AuthError('可用次数需为 1–1000，有效天数需为 1–365。')
        code = 'STITCH-' + secrets.token_hex(8).upper()
        iid, now = secrets.token_hex(12), int(time.time())
        hint, label = code[:7] + '••••' + code[-4:], str(body.get('label', ''))[:60].strip() or '邀请新成员'
        with self.connect() as db:
            db.execute('INSERT INTO invites(id,code_hash,hint,label,max_uses,expires_at,created_at,created_by) VALUES(?,?,?,?,?,?,?,?)',
                       (iid, digest(code), hint, label, maximum, now + days * 86400, now, user['id']))
            self._event(db, user['id'], 'invite_created', ip, label)
        return {'id': iid, 'code': code, 'max_uses': maximum, 'expires_at': now + days * 86400}

    def admin_action(self, user, body, ip):
        self.require_admin(user)
        action = body.get('action')
        if action == 'create_invite':
            return self.create_invite(user, body, ip)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if action == 'revoke_invite':
                if db.execute('UPDATE invites SET revoked=1 WHERE id=?', (str(body.get('id', '')),)).rowcount != 1:
                    raise AuthError('未找到邀请码。', 404)
                self._event(db, user['id'], 'invite_revoked', ip)
            elif action == 'set_user_disabled':
                target, disabled = str(body.get('user_id', '')), body.get('disabled')
                if type(disabled) is not bool:
                    raise AuthError('账号状态无效。')
                row = db.execute('SELECT username,role FROM users WHERE id=?', (target,)).fetchone()
                if not row:
                    raise AuthError('未找到账号。', 404)
                if row['role'] == 'admin':
                    raise AuthError('不能在这里停用管理员。')
                db.execute('UPDATE users SET disabled=? WHERE id=?', (int(disabled), target))
                if disabled:
                    db.execute('DELETE FROM sessions WHERE user_id=?', (target,))
                self._event(db, user['id'], 'user_disabled' if disabled else 'user_enabled', ip, row['username'])
            else:
                raise AuthError('未找到管理操作。', 404)
        return {'ok': True}


if __name__ == '__main__':
    import argparse
    import json
    parser = argparse.ArgumentParser(description='Initialize a new private studio account store once.')
    parser.add_argument('--db', required=True)
    parser.add_argument('--projects', required=True)
    parser.add_argument('--bootstrap-file', required=True, help='Private JSON file with user and password; never pass the password as an argument.')
    args = parser.parse_args()
    credentials_file = Path(args.bootstrap_file)
    if credentials_file.stat().st_mode & 0o077:
        raise SystemExit('账号文件必须仅所有者可读（权限 600）。')
    credentials = json.loads(credentials_file.read_text())
    ids = [p.parent.name for p in Path(args.projects).glob('*/project.json')]
    store = AuthStore(args.db)
    admin = store.bootstrap(credentials['user'], credentials['password'], ids)
    print(json.dumps({'initialized': True, 'username': admin['username'], 'migrated_projects': len(ids)}))

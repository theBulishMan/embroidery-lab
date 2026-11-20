from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit,unquote
from http.cookies import SimpleCookie
import argparse,json,mimetypes,threading,os
import studio_service as service
from studio_auth import AuthStore,AuthError,SESSION_SECONDS

WEB=Path(__file__).parent/'studio-web'
PUBLIC_NAMES={'input.png','generated.png','flat.png','fit-changes.png','preview.json','layers.svg','embroidery.svg','dimensions.svg','design.dst','design.pes','threads.csv','stitch-preview.png','validation.json','embroidery-project.zip','README.txt','region-settings.json'}
PUBLIC_NAMES.update({'reference.png','reference.jpg','reference.webp','original.png','original.jpg','original.webp'})
PUBLIC_ORIGIN=os.environ.get('EMBROIDERY_PUBLIC_ORIGIN','').rstrip('/')
AUTH_REQUIRED=os.environ.get('EMBROIDERY_AUTH_REQUIRED','1' if PUBLIC_ORIGIN else '0')=='1'
COOKIE_NAME='__Host-stitch_session' if PUBLIC_ORIGIN.startswith('https:') else 'stitch_session'
PUBLIC_WEB={'login.html','auth.css','auth.js','login-art.svg'}
PRIVATE_WEB={'index.html','studio.css','studio.js','preview.js','local-editor.js','art-editor.js','annotation-canvas.js','wizard.js','stitch-timeline.js','thread-profile.js','detail-fit.js','upload-treatment.js','production-assessment.js','coverage.js','coverage-worker.js','split-editor.js','admin.html','admin.js','account.html','account.js'}
if PUBLIC_ORIGIN and (urlsplit(PUBLIC_ORIGIN).scheme not in ('http','https') or
                      not urlsplit(PUBLIC_ORIGIN).netloc or urlsplit(PUBLIC_ORIGIN).path):
    raise ValueError('公开网址配置需要是完整的来源地址，不含路径。')

class StudioHTTPServer(ThreadingHTTPServer):
    """Bound request threads too; polling is not a background compute task."""
    daemon_threads=True
    def __init__(self,*args,**kwargs):
        self.auth=AuthStore(os.environ.get('EMBROIDERY_AUTH_DB',str(service.DATA/'accounts.sqlite'))) if AUTH_REQUIRED else None
        if self.auth and not self.auth.initialized():
            raise RuntimeError('请先初始化管理员账号，网站不会在未配置账号时公开作品。')
        self.request_slots=threading.BoundedSemaphore(16)
        super().__init__(*args,**kwargs)
    def process_request(self,request,client_address):
        if not self.request_slots.acquire(blocking=False):
            try:
                request.settimeout(1)
                request.sendall(b'HTTP/1.1 503 Service Unavailable\r\nRetry-After: 3\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            finally:self.shutdown_request(request)
            return
        request.settimeout(20)
        try:super().process_request(request,client_address)
        except Exception:
            self.request_slots.release();raise
    def process_request_thread(self,request,client_address):
        try:super().process_request_thread(request,client_address)
        finally:self.request_slots.release()

class Handler(BaseHTTPRequestHandler):
    def log_message(self,format,*args):pass
    def host_ok(self):
        port=self.server.server_port
        return self.headers.get('Host','') in [f'127.0.0.1:{port}',f'localhost:{port}',
            *([urlsplit(PUBLIC_ORIGIN).netloc] if PUBLIC_ORIGIN else [])]
    def send_headers(self,code,content_type,length,extra=None):
        self.send_response(code);self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(length));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer');self.send_header('X-Frame-Options','SAMEORIGIN')
        if code in (429,503):self.send_header('Retry-After','3')
        for name,value in (extra or {}).items():self.send_header(name,value)
        self.end_headers()
    def reply(self,code,payload,extra=None):
        raw=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode();self.send_headers(code,'application/json; charset=utf-8',len(raw),extra);self.wfile.write(raw)
    def redirect(self,target):
        self.send_headers(303,'text/plain; charset=utf-8',0,{'Location':target})
    def token(self):
        cookie=SimpleCookie()
        try:cookie.load(self.headers.get('Cookie',''))
        except Exception:return ''
        return cookie[COOKIE_NAME].value if COOKIE_NAME in cookie else ''
    def session_cookie(self,token):
        return f'{COOKIE_NAME}={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={SESSION_SECONDS if token else 0}'+('; Secure' if PUBLIC_ORIGIN.startswith('https:') else '')
    def current_user(self):
        return self.server.auth.current_user(self.token()) if self.server.auth else None
    def require_user(self):
        user=self.current_user()
        if self.server.auth and not user:raise AuthError('登录已失效，请重新登录。',401)
        return user
    def require_project(self,pid,user):
        service.identifier(pid)
        if self.server.auth:self.server.auth.require_project(pid,user)
    def client_ip(self):
        # Only trust the local reverse proxy, which replaces X-Real-IP.
        value=self.headers.get('X-Real-IP',self.client_address[0]) if self.client_address[0] in ('127.0.0.1','::1') and PUBLIC_ORIGIN else self.client_address[0]
        return value[:80]
    def file(self,path):
        if not path.is_file():self.reply(404,{'error':'未找到文件。'});return
        raw=path.read_bytes();mime=mimetypes.guess_type(path.name)[0] or 'application/octet-stream';self.send_headers(200,mime,len(raw));self.wfile.write(raw)
    def do_GET(self):
        if not self.host_ok():self.reply(403,{'error':'仅支持本机访问。'});return
        route=unquote(urlsplit(self.path).path)
        try:
            if route in ('/login','/login.html'):
                if self.current_user():self.redirect('/');return
                self.file(WEB/'login.html');return
            if route.removeprefix('/') in PUBLIC_WEB:
                self.file(WEB/route.removeprefix('/'));return
            if route=='/api/auth':
                self.reply(200,{'user':self.current_user(),'enabled':bool(self.server.auth),'invite_required':True});return
            user=self.current_user()
            if self.server.auth and not user:
                if route in ('/','/index.html','/admin','/admin.html','/account','/account.html'):
                    self.redirect('/login'+('?next=admin' if route.startswith('/admin') else '?next=account' if route.startswith('/account') else ''));return
                raise AuthError('请先登录。',401)
            if route=='/api/admin':
                if not self.server.auth:raise AuthError('此环境未启用账号管理。',404)
                self.reply(200,self.server.auth.admin_view(user));return
            if route=='/api/health':self.reply(200,{'status':'ok',**service.SCHEDULER.status()});return
            if route=='/api/projects':
                items=service.list_projects()
                if self.server.auth:
                    owned=self.server.auth.project_ids(user);items=[p for p in items if p['id'] in owned]
                self.reply(200,{'projects':items,'engine_version':service.ENGINE_VERSION});return
            if route.startswith('/api/projects/'):
                pid=route.rsplit('/',1)[1];self.require_project(pid,user)
                self.reply(200,service.project_view(service.read_project(pid)));return
            if route.startswith('/api/jobs/'):
                job=service.job_view(route.rsplit('/',1)[1]);self.require_project(job['project_id'],user)
                self.reply(200,job);return
            if route.startswith('/assets/'):
                relative=route.removeprefix('/assets/');parts=relative.split('/')
                if any(p.startswith('.') or not p for p in parts) or parts[-1] not in PUBLIC_NAMES:raise ValueError('资源路径无效。')
                self.require_project(parts[0],user)
                path=(service.PROJECTS/relative).resolve()
                if not path.is_relative_to(service.PROJECTS.resolve()):raise ValueError('资源路径无效。')
                self.file(path);return
            name={'/':'index.html','/admin':'admin.html','/account':'account.html'}.get(route,route.removeprefix('/'))
            if name in ('admin.html','admin.js') and self.server.auth:self.server.auth.require_admin(user)
            if name not in PRIVATE_WEB:self.reply(404,{'error':'未找到页面。'});return
            self.file(WEB/name)
        except AuthError as e:self.reply(e.status,{'error':str(e)})
        except (ValueError,FileNotFoundError):self.reply(404,{'error':'未找到作品或文件。'})
        except Exception:self.reply(503,{'error':'服务暂时繁忙，请稍后再试。'})
    def do_POST(self):
        port=self.server.server_port;origin=self.headers.get('Origin')
        origins=[f'http://127.0.0.1:{port}',f'http://localhost:{port}',*([PUBLIC_ORIGIN] if PUBLIC_ORIGIN else [])]
        if not self.host_ok() or (origin and origin not in origins):self.reply(403,{'error':'请求来源未授权。'});return
        try:
            route=urlsplit(self.path).path
            user=None if route=='/api/auth' else self.require_user()
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError('请求格式无效。')
            size=int(self.headers.get('Content-Length','0'))
            limit=8192 if route in ('/api/auth','/api/admin') or route.endswith('/sewouts') else 15_000_000
            if not 1<=size<=limit:raise ValueError('请求过大，请缩短输入或使用更小的图片。')
            body=json.loads(self.rfile.read(size))
            if not isinstance(body,dict):raise ValueError('请求格式无效。')
            if route=='/api/auth':
                auth=self.server.auth
                if not auth:raise AuthError('此环境未启用账号登录。',404)
                if body.get('action')=='logout':
                    auth.logout(self.token());self.reply(200,{'ok':True},{'Set-Cookie':self.session_cookie('')});return
                if body.get('action')=='password':
                    token=auth.change_password(self.require_user(),body,self.client_ip())
                    self.reply(200,{'ok':True},{'Set-Cookie':self.session_cookie(token)});return
                user,token=auth.authenticate(body,self.client_ip())
                self.reply(200,{'user':user},{'Set-Cookie':self.session_cookie(token)});return
            if route=='/api/admin':
                if not self.server.auth:raise AuthError('此环境未启用账号管理。',404)
                self.reply(200,self.server.auth.admin_action(user,body,self.client_ip()));return
            parts=urlsplit(self.path).path.strip('/').split('/')
            if parts==['api','projects']:
                claim=(lambda pid:self.server.auth.claim_project(pid,user)) if self.server.auth else None
                self.reply(202,service.new_project(body,owner_callback=claim));return
            if len(parts)==4 and parts[:2]==['api','projects']:
                self.require_project(parts[2],user)
                if parts[3]=='sewouts':self.reply(200,service.record_sewout(parts[2],body));return
                if parts[3]=='references':self.reply(201,service.upload_reference(parts[2],body));return
                if parts[3]=='size-advice':self.reply(200,service.size_advice(parts[2],body));return
                if parts[3]=='regions':self.reply(200,service.region_catalog(parts[2],body));return
                if parts[3]=='craft-advice':self.reply(202,service.advise_project(parts[2],body));return
                if parts[3]=='draft-review':self.reply(200,service.draft_review(parts[2],body));return
                if parts[3]=='revise-draft':self.reply(202,service.revise_draft(parts[2],body));return
                if parts[3]=='split-advice':self.reply(202,service.split_project(parts[2],body));return
                if parts[3]=='preserve-details':self.reply(202,service.preserve_details_project(parts[2],body));return
                if parts[3]=='fit-details':self.reply(202,service.fit_project(parts[2],body));return
                if parts[3]=='versions':job=service.edit_project(parts[2],body)
                elif parts[3]=='palette':job=service.recolor_project(parts[2],body)
                elif parts[3]=='plans':job=service.plan_project(parts[2],body)
                else:self.reply(404,{'error':'未找到操作。'});return
                self.reply(202,job);return
            self.reply(404,{'error':'未找到操作。'})
        except AuthError as e:self.reply(e.status,{'error':str(e)})
        except service.BusyError as e:self.reply(429,{'error':str(e)[:180],'retry_after_seconds':3})
        except (ValueError,KeyError,TypeError) as e:self.reply(400,{'error':str(e)[:180]})
        except Exception:self.reply(500,{'error':'处理未完成，请检查图片或稍后重试。'})

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8876);args=parser.parse_args()
    if not AUTH_REQUIRED:service.seed_rabbit()
    server=StudioHTTPServer(('127.0.0.1',args.port),Handler)
    print(f'Embroidery studio: http://127.0.0.1:{args.port}/',flush=True);server.serve_forever()

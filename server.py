"""Loopback-only sample planner. Only the output directory is web-accessible."""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit, unquote
import json, math, subprocess, sys, threading

ROOT=Path(__file__).parent.resolve()
PUBLIC=ROOT/'rabbit-test-20261114'
LOCK=threading.Lock()
VERSION='approved-rounded-v4'

def plan(width, thread, spacing):
    key=f'w{width:g}-{thread.lower()}-s{round(spacing*1000)}'
    folder=PUBLIC/'plans'/key
    with LOCK:
        cached=folder/'viewer-data.json'
        version=folder/'planner-version.txt'
        if not cached.exists() or not version.exists() or version.read_text()!=VERSION:
            folder.mkdir(parents=True,exist_ok=True)
            steps=[['prepare_rabbit.py','--width',str(width),'--thread',thread,'--spacing',str(spacing)],
                   ['export_rabbit.py'],['finalize_rabbit.py']]
            for step in steps:
                process=subprocess.run([sys.executable,str(ROOT/step[0]),*step[1:],'--out',str(folder)],
                    cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=180)
                if process.returncode:
                    (folder/'planner-error.log').write_text(process.stdout+'\n'+process.stderr)
                    raise RuntimeError('这组参数排针失败，请调整尺寸或间距后重试。')
            version.write_text(VERSION)
        data=json.loads(cached.read_text())
        data['downloadBase']=f'/plans/{key}/'
        return data

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(PUBLIC),**kwargs)
    def end_headers(self):
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Cache-Control','no-store')
        super().end_headers()
    def do_GET(self):
        path=unquote(urlsplit(self.path).path)
        if any(part.startswith('.') for part in path.split('/')) or path.endswith(('.log','.npy')):
            self.send_error(404);return
        super().do_GET()
    def json_response(self, code, value):
        payload=json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()
        self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
    def do_POST(self):
        if self.path!='/api/plan':self.send_error(404);return
        origin=self.headers.get('Origin')
        if origin and origin not in ['http://127.0.0.1:8876','http://localhost:8876']:
            self.json_response(403,{'error':'仅支持本地页面调用。'});return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 1<=length<=2048:raise ValueError('请求过大或为空。')
            body=json.loads(self.rfile.read(length))
            width=float(body['width']);spacing=float(body['spacing']);thread=body['thread']
            if not math.isfinite(width) or not 25<=width<=140:raise ValueError('宽度范围为25–140 mm。')
            if not math.isfinite(spacing) or not .25<=spacing<=.60:raise ValueError('间距范围为0.25–0.60 mm。')
            if thread not in ['75D','105D']:raise ValueError('请选择75D或105D。')
            width=round(width,1);spacing=round(spacing,3)
            data=plan(width,thread,spacing)
        except (ValueError,KeyError,TypeError) as e:
            self.json_response(400,{'error':str(e)});return
        except Exception:
            self.json_response(500,{'error':'排针未完成，请调整参数后重试。'});return
        self.json_response(200,data)

if __name__=='__main__':
    server=ThreadingHTTPServer(('127.0.0.1',8876),Handler)
    print('Rabbit original-color planner: http://127.0.0.1:8876/',flush=True)
    server.serve_forever()

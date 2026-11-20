from pathlib import Path
import subprocess, zipfile, time, argparse

parser=argparse.ArgumentParser()
parser.add_argument('--out',type=Path,default=Path(__file__).parent/'rabbit-test-20261114')
args=parser.parse_args();base=args.out.resolve()
engine='/Users/chenwenjie/Library/Application Support/org.inkscape.Inkscape/config/inkscape/extensions/inkstitch/inkstitch.app/Contents/MacOS/inkstitch'
cmd=[engine,'--extension=zip','--format-dst=True','--format-pes=True','--format-threadlist=True','--custom-file-name=rabbit',str(base/'rabbit-embroidery.svg')]
start=time.time()
with (base/'machine-export.zip').open('wb') as out,(base/'engine-export.log').open('w') as err:
    p=subprocess.run(cmd,stdout=out,stderr=err,timeout=180)
if p.returncode or not zipfile.is_zipfile(base/'machine-export.zip'):
    raise RuntimeError((base/'engine-export.log').read_text())
with zipfile.ZipFile(base/'machine-export.zip') as z:
    z.extractall(base/'machine')
    print({'seconds':round(time.time()-start,2),'files':z.namelist()})

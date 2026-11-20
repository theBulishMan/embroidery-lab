"""Repeatable trial-file benchmark. Inputs intentionally do not require network/API credentials.
Usage: .venv/bin/python research/unattended/bench.py CODE_DIR OUT_DIR CASE... ."""
import json, os, sys, time, subprocess, hashlib
from pathlib import Path
from PIL import Image, ImageDraw
ROOT=Path(__file__).resolve().parents[2];code,out=map(lambda x:Path(x).resolve(),sys.argv[1:3]);sys.path.insert(0,str(code));out.mkdir(parents=True,exist_ok=True)
from studio_artwork import flatten
from studio_fit import fit_artwork
try:
 from studio_fit import unattended_fit_stop
except ImportError:  # the frozen baseline predates preflight
 unattended_fit_stop=None
from studio_digitize import make_plan,PRESETS
from studio_local import catalog
samples={ 'rabbit':('rabbit-reference/v-selected',30,'75D'), 'bow':('39b025a8ccef4760/v-751a8517da5d',50,'75D'), 'seal':('1a9531fd74014400/v-922a4229122f',60,'75D'), 'yellow':('25c550742a3c43bb/v-338236c5d225',20,'75D'), 'bee20':('c1034f2112f34385/v-fcc509463ce9',20,'105D'), 'bee20fine':('c1034f2112f34385/v-fcc509463ce9',20,'75D'), 'bee80':('c1034f2112f34385/v-fcc509463ce9',80,'75D'), 'calico':('c0ad836876c5491e/v-bc79f8d1b8ac',60,'75D'), 'sail':(None,45,'75D'),'pin':(None,32,'105D'),'medal':(None,42,'75D'),'stems':(None,28,'75D'),'camera':(None,48,'105D'),'flower':(None,32,'105D'),'micro':(None,20,'105D')}
from public_icons import SAMPLES as PUBLIC, TRANSFER, UNSEEN, VARIANTS, image as public_input
from holdout import SAMPLES as HOLDOUT, draw as holdout_drawing
from validation import SAMPLES as VALIDATION, draw as validation_drawing
from acceptance import SAMPLES as ACCEPTANCE, draw as acceptance_drawing
CORE=tuple(samples);samples.update(PUBLIC|TRANSFER|UNSEEN|VARIANTS);samples.update(ACCEPTANCE); samples.update(HOLDOUT);samples.update(VALIDATION)
ARCHIVED_FIRST={'rabbitfirst':('6c8dee9172f24ecc/v-14f0d70b0a5d',30,'75D'),'bowfirst':('39b025a8ccef4760/v-6ac1aeab116a',50,'75D'),'beefirst':('c1034f2112f34385/v-92d5099eefcb',58,'75D'),'sealfirst':('1e7dff8f8a1544c6/v-b562c25e2b8c',60,'75D')};samples.update(ARCHIVED_FIRST)
def pattern(name,filepath):
 if name in PUBLIC or name in TRANSFER or name in UNSEEN or name in VARIANTS:return public_input(name,filepath)
 im=Image.new('RGBA',(420,420));d=ImageDraw.Draw(im); k='#22394c';y='#efa948';b='#426f9b'; w='#fff4d4';r='#ca6368';clear=(0,0,0,0)
 if name=='sail':
  d.polygon([(66,272),(348,272),(294,320),(119,320)],fill=k);d.polygon([(193,73),(193,255),(72,255)],fill=y);d.polygon([(207,96),(332,255),(207,255)],fill=b)
 elif name=='pin':
  d.ellipse((77,58,341,322),fill=b);d.polygon([(87,242),(207,389),(330,237)],fill=b);d.ellipse((147,126,268,247),fill=clear)
 elif name=='medal':
  d.rounded_rectangle((46,42,374,378),48,fill=k);d.rounded_rectangle((68,64,352,356),32,fill=y);d.polygon([(211,104),(235,173),(308,173),(251,217),(273,286),(211,246),(149,286),(169,217),(113,173),(186,173)],fill=k)
 elif name=='stems':
  d.line([(82,330),(177,240),(250,145),(305,70)],fill=k,width=9);d.ellipse((118,148,178,239),fill=b);d.ellipse((232,142,330,173),fill=y);d.ellipse((72,243,129,294),fill=b)
 elif name=='camera':
  d.rounded_rectangle((43,105,377,313),32,fill=k);d.rounded_rectangle((101,79,184,136),15,fill=k);d.ellipse((151,142,279,269),fill=y);d.ellipse((178,169,252,242),fill=b);d.ellipse((267,141,291,165),fill=w)
 elif name=='flower':
  for box in [(163,52,252,178),(228,152,352,241),(166,231,254,362),(59,151,184,241)]:d.ellipse(box,fill=r)
  d.ellipse((169,158,249,239),fill=y)
 elif name=='micro':
  d.rounded_rectangle((25,25,395,395),40,fill=y);d.ellipse((85,100,335,320),fill=k);d.ellipse((97,112,323,308),fill=y)
  for i in range(15):d.line([(124+i*10,182),(140+i*10,241)],fill=k,width=3)
 elif name in ACCEPTANCE:acceptance_drawing(name,d)
 elif name in HOLDOUT:holdout_drawing(name,d)
 elif name in VALIDATION:validation_drawing(name,d)
 else:raise ValueError(name)
 im.save(filepath)
requested=sys.argv[3:] or ['--core'];groups={'--core':CORE,'--acceptance':tuple(ACCEPTANCE),'--holdout':tuple(HOLDOUT),'--archived-first':tuple(ARCHIVED_FIRST),'--validation':tuple(VALIDATION),'--public':tuple(PUBLIC),'--transfer':tuple(TRANSFER),'--audit':tuple(UNSEEN),'--variants':tuple(VARIANTS)}
for name in [n for arg in requested for n in groups.get(arg,(arg,))]:
 src,width,thread=samples[name];folder=out/name;folder.mkdir(exist_ok=True);t=time.monotonic()
 try:
  if src:source=ROOT/'studio-data/projects'/src
  else:
   input_file=folder/('input.jpg' if name.endswith('jpg') else 'input.png');pattern(name,input_file);source=folder/'separated';flatten(input_file,source,9,preserve_white=not name.endswith('jpg') or name=='whiteboardjpg',detail_mode='preserve',width=width,thread=thread)
  meta,view,fit=fit_artwork(source,folder/'fit',width,thread)
  stop=unattended_fit_stop(fit,(meta['height_px']-1)/max(1,meta['width_px']-1)) if unattended_fit_stop else None
  if stop:
   row=dict(status='preflight',score=None,reasons=[stop['message']],stitches=None,files=False,fit_stop=stop,width=width,thread=thread,source='existing_flat' if src else ('new_jpg' if name.endswith('jpg') else 'new_png'),source_sha=hashlib.sha256((source/'classes.npy' if src else input_file).read_bytes()).hexdigest(),seconds=round(time.monotonic()-t,1))
   (folder/'error.txt').unlink(missing_ok=True);(folder/'result.json').write_text(json.dumps(row,ensure_ascii=False));print(name,'preflight',stop,flush=True);continue
  c=catalog(folder/'fit',width,thread=thread);(folder/'catalog.json').write_text(json.dumps(c));satin=PRESETS[thread]['normal'];v=make_plan(folder/'fit',folder/'plan',width,thread,satin[0],satin_spacing=satin[1],adaptive_fill=True);res=subprocess.run(['node',str(Path(__file__).with_name('browser-assess.mjs')),str(code/'studio-web'),str(folder)],capture_output=True,text=True,timeout=180,check=False);
  if res.returncode:raise RuntimeError(res.stderr[:950]);
  (folder/'error.txt').unlink(missing_ok=True);row=json.loads(res.stdout);row.update(changed=fit['changed_area_mm2'],objects=len(c['regions']),source='existing_flat' if src else ('new_jpg' if name.endswith('jpg') else 'new_png'),source_sha=hashlib.sha256((source/'classes.npy' if src else input_file).read_bytes()).hexdigest(),width=width,thread=thread,seconds=round(time.monotonic()-t,1));(folder/'result.json').write_text(json.dumps(row,ensure_ascii=False));print(name,row['status'],row['score'],row['stitches'],row['reasons'],flush=True)
 except Exception as e:
  import traceback;traceback.print_exc();(folder/'result.json').unlink(missing_ok=True);(folder/'error.txt').write_text(str(e));print('ERROR',name,str(e)[:500],flush=True)

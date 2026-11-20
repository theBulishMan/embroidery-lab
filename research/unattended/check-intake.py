"""Send one real PNG through the default unattended service, using disposable project storage."""
import base64,hashlib,json,os,sys,tempfile,time
from pathlib import Path
from tempfile import TemporaryDirectory
source,destination=map(Path,sys.argv[1:3]); destination.mkdir(parents=True,exist_ok=True)
with TemporaryDirectory() as data:
 os.environ['EMBROIDERY_DATA_DIR']=data;os.environ['EMBROIDERY_CONFIG_FILE']=str(Path(data)/'no-provider-config');sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
 import studio_service as s
 raw=source.read_bytes();start=time.monotonic(); job=s.new_project({'mode':'flat','max_colors':9,'settings':{'width':20,'thread':'75D','fabric':'twill'},'auto_plan':True,'image_data':'data:image/png;base64,'+base64.b64encode(raw).decode()})
 while time.monotonic()-start<150:
  j=s.job_view(job['id'])
  if j['status'] in ('done','error'):break
  time.sleep(.25)
 assert j['status']=='done' and j['result'].get('plan_id'),(j['status'],j.get('message'),j.get('result'))
 r=j['result']; p=s.read_project(r['project_id']); v=s.version_of(p,r['version_id']);f=s.PROJECTS/p['id']/v['id']/'plans'/r['plan_id'];plan=json.loads((f/'preview.json').read_text());evidence={'single_job':len(list(s.JOBS.glob('*.json')))==1,'unattended':r['unattended'],'selected_version':p['current_version_id']==v['id'],'width_mm':plan['meta']['design_width_mm'],'readback_agrees':plan['stats']['sew_geometry_dst_pes_equal'],'stitches':plan['stats']['stitch_count'],'both_exports':all((f/name).is_file() for name in ['design.dst','design.pes','embroidery-project.zip']),'source_sha256':hashlib.sha256(raw).hexdigest(),'seconds':round(time.monotonic()-start,1)}
 assert all(evidence[key] for key in ('single_job','unattended','selected_version','readback_agrees','both_exports')) and evidence['width_mm']==20
 (destination/'service-intake.json').write_text(json.dumps(evidence,indent=2));print(json.dumps(evidence))

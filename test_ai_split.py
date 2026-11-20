import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from PIL import Image,ImageDraw
from shapely.ops import unary_union
from studio_artwork import flatten,extract_geometries
from studio_ai_split import candidate_splits,validate_split
from studio_local import prepare_regions

class DetailSplitTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  im=Image.new('RGBA',(400,260));d=ImageDraw.Draw(im)
  d.rectangle((10,20,155,230),fill='#426cb5');d.rectangle((240,20,385,230),fill='#426cb5');d.rectangle((130,105,265,145),fill='#426cb5')
  d.ellipse((55,70,95,110),fill=(0,0,0,0));d.line((265,60,350,145),fill='white',width=3)
  im.save(self.root/'source.png');flatten(self.root/'source.png',self.root/'art',4,detail_mode='preserve')
 def tearDown(self):self.tmp.cleanup()
 def test_thin_line_hole_and_region_identity_survive_scaling(self):
  a=extract_geometries(self.root/'art',30,True);b=extract_geometries(self.root/'art',100,True)
  self.assertEqual(a[3],b[3]);self.assertTrue(any(p.interiors for g in a[1] for p in g))
  white=next(i for i,c in enumerate(a[0]['colors']) if min(c['rgb'])>240)
  self.assertEqual(len(a[1][white]),1,'diagonal fine line must not fragment at pixel corners')
 def test_ai_split_changes_only_internal_boundaries_and_keeps_manual_settings(self):
  folder=self.root/'art';before=hashlib.sha256((folder/'classes.npy').read_bytes()).hexdigest()
  candidates,regions,cuts,overrides,_=candidate_splits(folder,50);self.assertTrue(candidates)
  fine=min(regions,key=lambda r:r['geometry'].area);overrides={fine['key']:{'method':'fill','angle':45}}
  raw={'selected':[{'candidate_id':candidates[0]['id'],'confidence':.9,'reason':'分离窄颈两侧','children':[]}]}
  result=validate_split(raw,folder,50,[],overrides,candidates)
  self.assertEqual(len(result['accepted']),1);self.assertGreater(result['after_count'],result['before_count'])
  self.assertLess(result['area_difference_mm2'],1e-8);self.assertEqual(result['object_overrides'],overrides)
  self.assertEqual(before,hashlib.sha256((folder/'classes.npy').read_bytes()).hexdigest())
  # The child remains editable and accepts a further manual cut / real params.
  children=result['accepted'][0]['children'];key=children[0]
  prepare_regions(folder,50,result['region_cuts'],{**overrides,key:{'method':'fill','angle':30}})
 def test_unknown_low_confidence_and_invalid_child_parameters_are_not_applied(self):
  folder=self.root/'art';c,_,_,_,_=candidate_splits(folder,50)
  for entry in [{'candidate_id':'C999','confidence':1,'reason':'bad'},
                {'candidate_id':c[0]['id'],'confidence':.5,'reason':'unsure'},
                {'candidate_id':c[0]['id'],'confidence':.9,'reason':'bad','children':[{'index':0,'method':'fill','angle':float('nan')}]}]:
   result=validate_split({'selected':[entry]},folder,50,[],{},c)
   self.assertFalse(result['accepted']);self.assertEqual(result['region_cuts'],[])
 def test_preserve_prompt_protects_ornaments_and_does_not_claim_recovery(self):
  from studio_service import style_prompt
  p=style_prompt('保留花纹',9,'upload','#00FF00','preserve')
  self.assertIn('不能把图案本身的花纹当作噪声',p);self.assertNotIn('删除不影响识别的毛发',p)

 def test_free_split_needs_no_provider_and_identical_ai_request_is_cached(self):
  import studio_service as s
  folder=self.root/'art';project={'id':'sample','original_file':'source.png','versions':[{'id':'art'}]}
  body={'version_id':'art','width':50,'thread':'75D','spacing':.2,'satin_spacing':.35,'fabric':'twill'}
  def synchronous(pid,kind,worker):return worker('check')
  with patch.object(s,'PROJECTS',self.root.parent),patch.object(s,'read_project',return_value=project),\
       patch.object(s,'submit',side_effect=synchronous),patch.object(s,'update_job'),\
       patch.object(s,'config',return_value={}),patch.object(s,'request_split') as provider:
   # Use the actual temp project folder name; only the provider is replaced.
   pid=self.root.name;project['id']=pid
   free=s.split_project(pid,{**body,'use_ai':False})['split_advice']
   self.assertEqual(free['usage']['model'],'local');provider.assert_not_called()
   provider.return_value=({'selected':[],'detail_notes':['待核对']},{'model':'test','elapsed_s':.1,'input_tokens':10,'output_tokens':5})
   first=s.split_project(pid,{**body,'use_ai':True})['split_advice']
   second=s.split_project(pid,{**body,'use_ai':True})['split_advice']
   self.assertEqual(provider.call_count,1);self.assertTrue(second['usage']['cached'])
   self.assertEqual(first['snapshot'],second['snapshot'])

 def test_many_holes_and_multiple_colors_survive_without_subject_rules(self):
  im=Image.new('RGBA',(400,300));d=ImageDraw.Draw(im)
  d.rectangle((20,20,260,280),fill='#222222')
  for x in (45,105,165,225):
   for y in (50,110,170,230):d.ellipse((x-10,y-10,x+10,y+10),fill=(0,0,0,0))
  d.polygon([(300,30),(320,100),(390,120),(320,140),(300,230),(280,140),(265,120),(280,100)],fill='#1879d8')
  source=self.root/'other.png';im.save(source);folder=self.root/'other'
  flatten(source,folder,4,detail_mode='preserve')
  info,groups,_=extract_geometries(folder,50)
  self.assertEqual(len(info['colors']),2);self.assertEqual(sum(len(p.interiors) for g in groups for p in g),16)
  from studio_craft import recommend_stitch
  self.assertTrue(all(recommend_stitch(p)['method'] in {'fill','satin','running'} for g in groups for p in g))

 def test_geometry_cache_does_not_leak_mutation_or_reuse_replaced_file(self):
  folder=self.root/'art';a=extract_geometries(folder,50,True)
  a[1][0].clear();a[0]['colors'].clear()
  b=extract_geometries(folder,50,True);self.assertTrue(b[1][0]);self.assertTrue(b[0]['colors'])
  pixels=np.load(folder/'classes.npy');pixels[:10]=255;np.save(folder/'classes.npy',pixels)
  c=extract_geometries(folder,50,True)
  self.assertLess(sum(p.area for g in c[1] for p in g),sum(p.area for g in b[1] for p in g))

if __name__=='__main__':unittest.main()

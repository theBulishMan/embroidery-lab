import base64,io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from PIL import Image,ImageDraw
import httpx
import studio_generation as generation
import studio_service as service
from studio_artwork import flatten

class ArtworkEditingTests(unittest.TestCase):
 def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
 def tearDown(self):self.temp.cleanup()
 def save(self,image,name='image.png'):
  p=self.root/name;image.save(p);return p
 def test_auxiliary_background_preserves_white_face_and_white_line(self):
  image=Image.new('RGB',(180,160),'#ff00ff');d=ImageDraw.Draw(image)
  d.ellipse((25,20,155,150),fill='white');d.ellipse((60,65,80,85),fill='black');d.line((0,40,60,40),fill='white',width=6)
  source=self.save(image);out=self.root/'flat';meta,_=flatten(source,out,5,background_color='#FF00FF')
  classes=np.load(out/'classes.npy');rgba=np.array(Image.open(out/'flat.png'))
  self.assertLess(classes.shape[0],160);self.assertEqual(meta['background_handling'],'auxiliary_color')
  self.assertTrue(any(c['name']=='白色' and c['pixel_count']>10000 for c in meta['colors']))
  self.assertFalse(any(c['hex']=='#FF00FF' for c in meta['colors']))
  self.assertEqual(rgba[20,0,3],255) # white line touches original edge, not deleted
 def test_opaque_white_requires_explicit_removal(self):
  im=Image.new('RGB',(100,100),'white');ImageDraw.Draw(im).ellipse((30,30,70,70),fill='black');src=self.save(im)
  meta,_=flatten(src,self.root/'preserve',3,preserve_white=True)
  self.assertEqual(meta['width_px'],100);self.assertIn('白色',[c['name'] for c in meta['colors']])
  meta,_=flatten(src,self.root/'remove',3)
  self.assertLess(meta['width_px'],100)
 def test_unmatched_background_fails_without_deleting_white(self):
  source=self.save(Image.new('RGB',(100,100),'white'))
  with self.assertRaisesRegex(ValueError,'没有自动删除白色'):flatten(source,self.root/'flat',4,background_color='#FF00FF')
 def test_reference_order_missing_slots_and_marks(self):
  self.assertIn('图二=第2张',generation.reference_prompt('参考@图二的颜色',2,[]))
  with self.assertRaises(ValueError):generation.reference_prompt('参考@图二',1,[])
  for marks in [[{'x':float('nan'),'y':.5,'radius':.1,'text':'改色'}],[{'x':.5,'y':.5,'radius':.1,'text':''}]]:
   with self.assertRaises(ValueError):generation.checked_marks(marks)
  valid=generation.checked_marks([{'x':.3,'y':.4,'radius':.15,'text':'改白色'}]);self.assertEqual(valid[0]['x'],.3)
 def test_provider_uses_original_multipart_files_and_separate_markup(self):
  a=self.save(Image.new('RGB',(230,170),'white'),'a.png');b=self.save(Image.new('RGB',(130,190),'blue'),'b.png');calls=[]
  def mock(request):
   calls.append(request);return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(a.read_bytes()).decode()}],'usage':{'total_tokens':10}})
  real=httpx.Client
  with patch.object(generation.httpx,'Client',side_effect=lambda **kw:real(transport=httpx.MockTransport(mock),**kw)):
   raw,info=generation.request_image({'OPENAI_BASE_URL':'https://example.invalid','OPENAI_API_KEY':'never-log-this'},'test',{},[a,b],[{'x':.5,'y':.5,'radius':.2,'text':'白色'}])
  request=calls[0];body=request.content
  self.assertEqual(str(request.url),'https://example.invalid/v1/images/edits')
  self.assertIn(b'name="image[]"',body);self.assertIn(a.read_bytes(),body);self.assertIn(b.read_bytes(),body)
  self.assertIn(b'name="markup"',body);self.assertIn(b'name="complexity"',body)
  self.assertNotIn(b'name="background"',body);self.assertNotIn(b'name="output_format"',body)
  self.assertEqual(raw,a.read_bytes());self.assertEqual(info['reference_count'],2)
 def test_upload_reference_preserves_original_and_rejects_foreign_versions(self):
  p=self.root/'test';p.mkdir();(p/'project.json').write_text(json.dumps({'id':'test','versions':[]}))
  data=io.BytesIO();Image.new('RGB',(2400,100),'white').save(data,'JPEG');raw=data.getvalue()
  with patch.object(service,'PROJECTS',self.root):
   ref=service.upload_reference('test',{'image_data':'data:image/jpeg;base64,'+base64.b64encode(raw).decode(),'name':'example.jpg'})
   self.assertEqual((p/ref['id']/ref['file']).read_bytes(),raw)
   paths,_=service.reference_files(service.read_project('test'),[{'kind':'upload','id':ref['id']}]);self.assertEqual(paths[0].read_bytes(),raw)
   with self.assertRaises(ValueError):service.reference_files(service.read_project('test'),[{'kind':'version','id':'v-foreign'}])
 def test_minimum_size_estimate_scales_with_small_regions(self):
  p=self.root/'test';v=p/'v-first';v.mkdir(parents=True);(p/'project.json').write_text(json.dumps({'id':'test','versions':[{'id':'v-first'}]}))
  im=Image.new('RGBA',(300,300));d=ImageDraw.Draw(im);d.ellipse((0,0,299,299),fill='white');d.ellipse((120,120,125,125),fill='black');flatten(self.save(im),v,3)
  with patch.object(service,'PROJECTS',self.root):
   advice=service.size_advice('test',{'version_id':'v-first'})
   self.assertGreaterEqual(advice['profiles']['105D']['width_mm'],advice['profiles']['75D']['width_mm'])
   self.assertGreater(len(advice['details']),0)
 def test_prompt_protects_white_and_requires_opaque_subject(self):
  prompt=service.style_prompt('增加白色圆脸',4,'edit','#FF00FF')
  self.assertIn('白色脸',prompt);self.assertIn('不得挖空或删除',prompt);self.assertIn('辅助色 #FF00FF',prompt)
 def test_text_project_uses_generation_endpoint_and_simplified_tracing(self):
  # Exercise the entire text -> provider -> flatten -> saved-version path.
  # An older client may still send preserve, but there is no source to trace.
  im=Image.new('RGB',(160,160),'#00ff00');d=ImageDraw.Draw(im)
  d.ellipse((25,20,135,140),fill='#ffc245');d.ellipse((60,60,75,80),fill='#222222')
  raw=self.save(im).read_bytes();calls=[];real=httpx.Client
  def provider(request):
   calls.append(request);payload=json.loads(request.content)
   self.assertEqual(request.url.path,'/v1/images/generations')
   self.assertEqual(payload['model'],'gpt-image-2.6-flare')
   self.assertIn('比耶的小女孩 半身像',payload['prompt'])
   self.assertIn('没有参考图',payload['prompt']);self.assertIn('最多4种纯色',payload['prompt'])
   self.assertNotIn('即使尺寸可能太小，也先保留',payload['prompt'])
   return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(raw).decode()}]})
  def submit(pid,kind,worker,**kwargs):return worker('test-job')
  with patch.object(service,'PROJECTS',self.root),patch.object(service.SCHEDULER,'check_capacity'),patch.object(service,'submit',side_effect=submit),patch.object(service,'update_job'),patch.object(service,'config',return_value={'OPENAI_BASE_URL':'https://example.invalid','OPENAI_API_KEY':'test-only'}),patch.object(generation.httpx,'Client',side_effect=lambda **kw:real(transport=httpx.MockTransport(provider),**kw)):
   result=service.new_project({'mode':'text','prompt':'比耶的小女孩 半身像','max_colors':4,'detail_mode':'preserve'})
   project=service.read_project(result['project_id']);v=project['versions'][0]
  self.assertEqual(len(calls),1);self.assertEqual(v['detail_mode'],'simplify')
  self.assertEqual(v['references'],[]);self.assertIsNone(project['source_url'])
  self.assertEqual(v['image_settings']['resolution'],'1K')
  self.assertLessEqual(v['color_count'],4);self.assertTrue((self.root/project['id']/v['id']/'flat.png').exists())

if __name__=='__main__':unittest.main()

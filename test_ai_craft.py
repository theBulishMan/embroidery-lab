import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image,ImageDraw
from lxml import etree as ET
from studio_artwork import flatten,STITCH
from studio_ai_craft import region_facts,validate_advice,request_advice,plan_visual_batches,ground_recognitions
from studio_local import prepare_regions
from studio_digitize import make_plan

class FineCraftTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        im=Image.new('RGBA',(300,240));d=ImageDraw.Draw(im)
        d.rounded_rectangle((10,10,200,230),radius=20,fill='#ffcc22')
        d.rectangle((250,50,255,200),fill='#121212');im.save(self.root/'input.png')
        self.folder=self.root/'art';flatten(self.root/'input.png',self.folder,3)
        self.facts,self.cuts,self.overrides,_=region_facts(self.folder,30,[],{})
        self.fine=next(r['id'] for r in self.facts if 'run' in r['allowed_methods'])
        self.wide=next(r['id'] for r in self.facts if r['allowed_methods']==['fill'])
    def tearDown(self):self.temp.cleanup()

    def test_targeted_review_sends_only_the_chosen_region_and_protects_overrides(self):
        usage={'input_tokens':4,'output_tokens':2,'elapsed_s':.02}
        config={'ALIBABA_API_KEY':'test','ALIBABA_BASE_URL':'https://example.com','ALIBABA_VISION_MODEL':'vision','ALIBABA_PLANNER_MODEL':'planner'}
        settings={'width':30,'thread':'75D','fabric':'twill'}
        def provider(_config,stage,model,_system,content,_limit):
            if model=='vision':
                self.assertEqual(stage,'看图识别')
                self.assertEqual(len(json.loads(content[0]['text'])['选区']),1)
                return {'regions':[{'tag':'R01','part':'边线','role':'detail','confidence':.93}]},usage
            self.assertEqual(stage,'选择针法')
            self.assertEqual([r['id'] for r in json.loads(content)['regions']],[self.fine])
            return {'regions':[{'id':self.fine,'settings':{'method':'running'},'reason':'沿线走针'}]},usage
        with patch('studio_ai_craft.call_model',side_effect=provider) as calls:
            def run(selected,overrides=None):
                prepared=prepare_regions(self.folder,30,[],overrides or {})
                return plan_visual_batches(config,self.folder,None,settings,'',prepared,self.root/'target',lambda _:None,selected)
            report=run(self.fine)
            self.assertEqual(report['region_id'],self.fine)
            self.assertEqual([r['id'] for r in report['suggestions']],[self.fine])
            self.assertEqual(report['reviewed_count'],1)
            with self.assertRaises(ValueError):run('no-such-region')
            with self.assertRaises(ValueError):run(self.fine,{self.fine:{'method':'running'}})
            self.assertEqual(calls.call_count,2)

    def test_ai_protects_manual_and_rejects_unsupported_or_wrong_geometry(self):
        data={'regions':[{'id':self.wide,'settings':{'method':'fill','angle':80},'reason':'宽面'},
                         {'id':self.fine,'settings':{'method':'running','running_stitch_length':1},'reason':'细线'}]}
        locked={self.wide:{'method':'fill','angle':25}}
        result=validate_advice(data,self.folder,30,[],locked,self.facts)
        self.assertEqual([r['id'] for r in result['suggestions']],[self.fine])
        self.assertEqual(locked[self.wide]['angle'],25)
        for setting in [{'method':'satin'},{'method':'fill','angle':float('nan')},{'method':'ripple'},
                        {'method':'fill','fill_spacing':.01},{'method':'fill','bean_repeats':1}]:
            with self.subTest(setting=setting),self.assertRaises(ValueError):
                validate_advice({'regions':[{'id':self.wide,'settings':setting,'reason':'test'}]},self.folder,30,[],{},self.facts)

    def test_running_integers_and_method_specific_fields_validated(self):
        for setting in [{'method':'run','bean_repeats':1},{'method':'running','running_repeats':1.5},
                        {'method':'fill','running_tolerance':.1},{'method':'running','bean_repeats':4}]:
            with self.subTest(setting=setting),self.assertRaises(ValueError):prepare_regions(self.folder,30,overrides={self.fine:setting})

    def test_small_inlays_use_verified_location_and_protect_subthread_specks(self):
        facts=[{'id':'petal','bbox_mm':[16,9,16.5,9.8],'inside':'wing','host_name':'白色','color':'藏青'},
               {'id':'speck','bbox_mm':[14,9,14.12,9.15],'inside':'wing','host_name':'白色','color':'藏青'}]
        proposals={f['id']:{'part':'前足','role':'edge','split_hint':'cut'} for f in facts}
        valid=ground_recognitions(proposals,facts,20,'75D')
        self.assertEqual(set(valid),{'petal'});self.assertEqual(valid['petal']['role'],'detail')
        self.assertIn('白色区内',valid['petal']['part']);self.assertNotIn('足',valid['petal']['part'])

    def test_visual_batches_only_charge_unlocked_shapes_and_reuse_successful_stages(self):
        initial={self.wide:{'method':'fill','angle':25}}
        prepared=prepare_regions(self.folder,30,[],initial)
        settings={'width':30,'thread':'75D','fabric':'twill'}
        usage={'input_tokens':10,'output_tokens':5,'elapsed_s':.01}
        def model(_config,_stage,name,_system,content,_limit):
            if name=='vision':
                self.assertEqual([r['type'] for r in content],['text','image_url','image_url'])
                return {'regions':[{'tag':'R01','part':'细线','role':'detail','confidence':.88}]},usage
            return {'regions':[{'id':self.fine,'settings':{'method':'running','pull_compensation':.8},'reason':'沿线往返'}]},usage
        config={'ALIBABA_API_KEY':'test','ALIBABA_BASE_URL':'https://example.com','ALIBABA_VISION_MODEL':'vision','ALIBABA_PLANNER_MODEL':'planner'}
        with patch('studio_ai_craft.call_model',side_effect=model) as calls:
            args=(config,self.folder,None,settings,'',prepared,self.root/'cache',lambda _:None)
            first=plan_visual_batches(*args);second=plan_visual_batches(*args)
            self.assertEqual(calls.call_count,2)
        self.assertEqual(first['suggestions'],second['suggestions'])
        self.assertEqual(first['suggestions'][0]['settings'],{'method':'running','source':'ai','sewing_role':'detail'})
        self.assertEqual(first['protected_count'],1);self.assertEqual(second['usage']['calls'],0)

    def test_single_and_bean_controls_reach_real_machine_export(self):
        single={'method':'run','running_stitch_length':1.2,'running_tolerance':.04,'running_repeats':1}
        bean={'method':'running','running_stitch_length':1.2,'running_tolerance':.04,'running_repeats':2,'bean_repeats':1}
        a=make_plan(self.folder,self.root/'single',30,'75D',.2,object_overrides={self.fine:single})
        b=make_plan(self.folder,self.root/'bean',30,'75D',.2,object_overrides={self.fine:bean})
        xml=ET.parse(str(self.root/'bean/embroidery.svg'))
        path=xml.xpath('//*[@data-role="细线走针"]')[0]
        for name,value in {'running_stitch_length_mm':'1.2','running_stitch_tolerance_mm':'0.04','repeats':'2','bean_stitch_repeats':'1'}.items():
            self.assertEqual(path.get(f'{{{STITCH}}}{name}'),value)
        self.assertTrue(b['stats']['sew_geometry_dst_pes_equal'])
        self.assertGreater(b['stats']['stitch_count'],a['stats']['stitch_count'])
        self.assertEqual(a['meta']['separated_objects'],b['meta']['separated_objects'])

    def test_provider_error_never_exposes_credentials_or_response(self):
        class Response:
            is_success=False;status_code=401
        class Client:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def post(self,*args,**kwargs):return Response()
        with patch('studio_ai_provider.httpx.Client',return_value=Client()),self.assertRaisesRegex(ValueError,'选择针法.*401.*密钥无效') as caught:
            request_advice({'ALIBABA_API_KEY':'secret-do-not-print','ALIBABA_BASE_URL':'https://example.com','ALIBABA_PLANNER_MODEL':'model'},self.facts,{})
        self.assertNotIn('secret-do-not-print',str(caught.exception))

    def test_interrupted_vision_request_names_stage_and_never_resends(self):
        import httpx
        from studio_ai_provider import call_model
        config={'ALIBABA_API_KEY':'private','ALIBABA_BASE_URL':'https://example.com','ALIBABA_NETWORK_MODE':'direct'}
        with patch('studio_ai_provider.httpx.Client') as client:
            post=client.return_value.__enter__.return_value.post
            post.side_effect=httpx.RemoteProtocolError('private transport details')
            with self.assertRaisesRegex(ValueError,'看图识别.*接收响应时连接断开') as error:
                call_model(config,'看图识别','model','instructions',[],100)
        post.assert_called_once()
        self.assertFalse(client.call_args.kwargs['trust_env'])
        self.assertNotIn('private',str(error.exception))

if __name__=='__main__':unittest.main()

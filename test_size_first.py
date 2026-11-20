import base64
import io
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw
from shapely import affinity
from shapely.geometry import Point, box
from shapely.ops import unary_union

import studio_service as studio
from studio_quality import review_needle_geometry
from studio_fit import separate_lobes
from studio_revision import review_draft


class SizeFirstTests(unittest.TestCase):
    def setUp(self):
        stack = self.enterContext(ExitStack())
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        stack.enter_context(patch.object(studio, 'PROJECTS', self.root))
        stack.enter_context(patch.object(studio.SCHEDULER, 'check_capacity'))
        stack.enter_context(patch.object(studio, 'update_job'))
        stack.enter_context(patch.object(studio, 'submit', side_effect=lambda pid, kind, work: work('job')))
        image = Image.new('RGBA', (321, 241))
        draw = ImageDraw.Draw(image)
        draw.rectangle((10, 20, 310, 220), fill='#ebc855')
        draw.ellipse((85, 75, 95, 85), fill='#202630')
        draw.rectangle((190, 90, 220, 118), fill='#315a98')
        data = io.BytesIO()
        image.save(data, 'PNG')
        self.raw = data.getvalue()
        self.uri = 'data:image/png;base64,' + base64.b64encode(self.raw).decode()

    def make(self, width, mode='flat'):
        return studio.new_project(dict(mode=mode, prompt='保留主花瓣', image_data=self.uri,
                                  settings={'width':width, 'thread':'75D'}, max_colors=5))

    def version(self, result):
        return studio.version_of(studio.read_project(result['project_id']),result['version_id'])

    def test_paid_artwork_survives_automatic_plan_failure_without_second_call(self):
        request=dict(mode='text', prompt='一枚小帆船', settings={'width':35,'thread':'105D'}, auto_plan=True)
        with patch.object(studio,'request_image',return_value=(self.raw,{'generation_seconds':0})) as provider, patch.object(studio,'config'), patch.object(studio,'plan_project',side_effect=RuntimeError('failed to export')):
            result=studio.new_project(request)
        self.assertTrue(result['plan_pending'])
        self.assertEqual(provider.call_count,1)
        project=studio.read_project(result['project_id'])
        self.assertEqual(project['current_version_id'],result['version_id'])
        self.assertTrue((self.root/project['id']/project['versions'][0]['id']/'classes.npy').is_file())
        self.assertNotIn('plan_id',result)

    def test_lost_source_patterns_stop_automatic_work_without_losing_the_drawing(self):
        fit = studio.fitted_version
        def extensive_loss(*args, **kwargs):
            version = fit(*args, **kwargs)
            return {**version, 'fit_report': {**version['fit_report'], 'omitted_motifs': [
                {'count':5, 'area_mm2':1.5} for _ in range(6)]}}
        with patch.object(studio, 'fitted_version', side_effect=extensive_loss), patch.object(studio, 'plan_project') as machinery:
            result=studio.new_project(dict(mode='flat',image_data=self.uri,auto_plan=True,settings={'width':20,'thread':'75D'}))
        machinery.assert_not_called()
        self.assertTrue(result['plan_pending'])
        self.assertEqual(result['fit_stop']['parts'],30)
        project=studio.read_project(result['project_id'])
        self.assertEqual(project['current_version_id'],result['version_id'])
        self.assertTrue((self.root/project['id']/project['versions'][0]['id']/'classes.npy').is_file())

    def test_no_fitting_job_is_started_for_missing_automatic_size(self):
        with patch.object(studio,'request_image') as provider:
            with self.assertRaises(ValueError):studio.new_project(dict(mode='text',prompt='图案',auto_plan=True))
            with self.assertRaises(ValueError):studio.new_project(dict(mode='text',prompt='图案',auto_plan='false'))
            provider.assert_not_called()
        self.assertEqual(list(self.root.iterdir()),[])

    def test_import_and_enlargement_use_original_at_physical_scale(self):
        small = self.make(20)
        project = studio.read_project(small['project_id'])
        source, fitted = project['versions']
        self.assertGreater(fitted['fit_report']['removed_parts'], 0)
        self.assertEqual(source['id'], fitted['parent_id'])
        source_folder = self.root/project['id']/source['id']
        before = (source_folder/'classes.npy').read_bytes()
        actual = json.loads((source_folder/'preview.json').read_text())['meta']
        self.assertEqual((actual['design_width_mm'], actual['design_height_mm']), (20,13.33))
        self.assertEqual(actual['thread_spec'], '75D')
        up = studio.fit_project(project['id'],dict(parent_version_id=fitted['id'],width=80,thread='75D'))
        fresh = self.make(80)
        np.testing.assert_array_equal(
            np.load(self.root/project['id']/up['version_id']/'classes.npy'),
            np.load(self.root/fresh['project_id']/fresh['version_id']/'classes.npy'))
        self.assertEqual(self.version(up)['parent_id'], source['id'])
        self.assertEqual(self.version(fresh)['fit_report']['removed_parts'], 0)
        self.assertEqual(len(studio.read_project(fresh['project_id'])['versions']),1)
        self.assertEqual(before,(source_folder/'classes.npy').read_bytes())

    def test_model_receives_scale_on_generation_repair_and_edit(self):
        with patch.object(studio,'request_image',return_value=(self.raw,{'generation_seconds':0})) as provider, patch.object(studio,'config'):
            for mode,width,relative in [('text',20,'4.00%'),('upload',80,'1.00%')]:
                result = self.make(width,mode)
                prompt = provider.call_args.args[1]
                self.assertIn(f'成品主体宽度 {width} mm',prompt)
                self.assertIn(relative,prompt)
                self.assertEqual(self.version(result)['preferred_settings']['width'],width)
            result = studio.edit_project(result['project_id'],dict(parent_version_id=result['version_id'],prompt='修改颜色'))
            self.assertIn('成品主体宽度 80 mm',provider.call_args.args[1])
            self.assertEqual(self.version(result)['preferred_settings']['thread'],'75D')
            self.assertEqual(provider.call_count,3)

    def test_size_is_validated_before_project_or_billable_call(self):
        with patch.object(studio,'request_image') as provider:
            with self.assertRaises(ValueError):self.make(12,'text')
            with self.assertRaises(ValueError):studio.new_project(dict(mode='text',prompt='兔子',settings={'thread':'75D'}))
            provider.assert_not_called()
            self.assertEqual(list(self.root.iterdir()),[])

    def test_needle_envelope_excludes_jumps_and_detects_overshoot(self):
        region = {'key':'sample','color':0,'geometry':box(5,5,25,15)}
        view={'width':30,'height':20,'meta':{'design_width_mm':20,'design_height_mm':10,
              'preview_thread_width_mm':.20,'export_origin_mm':[15,10]},
              'commands':[[2,2,1,0],[5,5,0,0],[25.4,15,0,0],[29,19,1,0]]}
        envelope=review_needle_geometry(view,[region])['needle_envelope']
        self.assertEqual(envelope['width_mm'],20.4)
        self.assertEqual(envelope['outside_mm'],{'左':0,'右':.4,'上':0,'下':0})
        self.assertTrue(envelope['needs_review'])

    def test_revision_separates_artwork_evidence_from_unmeasured_needles(self):
        version={'aspect_ratio':.8,'preferred_settings':{'width':25,'thread':'75D'},'fit_report':{
            'width':25,'thread':'75D','omitted_motifs':[{'count':5,'bbox_mm':[6,7,9,12],'color':0}]}}
        review=review_draft(version,None,{},set())
        self.assertEqual((review['route'],review['height'],review['motifs']),('artwork',20,5))
        self.assertIsNone(review['gap_bridges'])
        self.assertIsNone(review['score'])
        self.assertEqual(review['items'][0]['locations'][0]['point'],[7.5,9.5])

    def test_revision_uses_actual_gap_and_color_targets_and_preserves_manual_work(self):
        version={'aspect_ratio':1}
        plan={'width':30,'thread':'105D','object_overrides':{'outer':{'method':'satin'}},
              'quality_review':{'gap_bridges':[{'first':'outer','gap_mm':.52}],
                    'stitch_suitability':{'issues':[{'region_key':'fan','reason':'扇面落针方向失配'}]}}}
        review=review_draft(version,plan,{'bare':3,'color':7,'bare_ids':['unknown','base'],
            'color_ids':['edge'],'score':79},{'base','edge','outer','fan'})
        targets={item['kind']:item['region'] for item in review['items']}
        self.assertEqual((targets['coverage'],targets['overlap']),('base','edge'))
        self.assertEqual((review['route'],review['targets']),('craft',['fan','base','edge']))
        self.assertIn('扇面',next(item['text'] for item in review['items'] if item['kind']=='direction'))
        incomplete=review_draft(version,{'width':30,'thread':'105D'},{'score':39},set())
        self.assertIsNone(incomplete['route'])
        self.assertTrue(incomplete['items'])

    def test_identical_automatic_revision_reuses_saved_alternative(self):
        made=self.make(20);pid=made['project_id'];vid=made['version_id']
        project=studio.read_project(pid)
        v=studio.version_of(project,vid)
        v['fit_report']={'width':20,'thread':'75D','omitted_motifs':[{'count':4,'color':1,'bbox_mm':[6,6,9,9]}]}
        studio.save_json(self.root/pid/'project.json',project)
        body={'version_id':vid,'plan_id':None}
        with patch.object(studio,'generate_version',return_value={'project_id':pid,'version_id':'saved-edit'}) as image:
            first=studio.revise_draft(pid,body)
            same=studio.revise_draft(pid,body)
        self.assertEqual(image.call_count,1)
        self.assertEqual((first['version_id'],same['version_id']),('saved-edit','saved-edit'))
        self.assertTrue(same['cached'])
        saved=studio.read_project(pid)
        self.assertEqual(len(saved['revisions']),1)
        self.assertIn(vid,[version['id'] for version in saved['versions']])

    def test_retry_after_machine_export_failure_only_rebuilds_the_saved_artwork(self):
        made=self.make(20);pid=made['project_id'];vid=made['version_id']
        project=studio.read_project(pid)
        studio.version_of(project,vid)['fit_report']={'width':20,'thread':'75D',
            'omitted_motifs':[{'count':4,'color':1,'bbox_mm':[6,6,9,9]}]}
        studio.save_json(self.root/pid/'project.json',project)
        body={'version_id':vid}
        pending={'project_id':pid,'version_id':vid,'plan_pending':True}
        done={'project_id':pid,'version_id':vid,'plan_id':'stitch-retry'}
        with patch.object(studio,'generate_version',return_value=pending) as image, patch.object(studio,'finish_artwork',return_value=done) as export:
            self.assertTrue(studio.revise_draft(pid,body)['plan_pending'])
            self.assertEqual(studio.revise_draft(pid,body)['plan_id'],'stitch-retry')
            self.assertTrue(studio.revise_draft(pid,body)['cached'])
        self.assertEqual((image.call_count,export.call_count),(1,1))
        self.assertEqual(len(studio.read_project(pid)['revisions']),1)

    def test_keeps_main_petals_and_does_not_split_a_major_band_for_a_tiny_spur(self):
        lobe=affinity.scale(Point(0,0).buffer(1),xfact=.53,yfact=.27)
        petals=[affinity.rotate(affinity.translate(lobe,xoff=1.02),angle,origin=(0,0))
                for angle in range(0,360,45)]
        necks=[affinity.rotate(box(0,-.035,.65,.035),angle,origin=(0,0)) for angle in range(0,360,45)]
        motif=unary_union([*necks,*petals])
        pieces=separate_lobes(motif,.4,.20)
        self.assertIsNotNone(pieces)
        self.assertTrue(all(pieces.covers(p.centroid) for p in petals))
        stripe=unary_union([box(0,0,9,1.2),box(9,.53,9.6,.65),Point(9.7,.6).buffer(.32)])
        self.assertIsNone(separate_lobes(stripe,.4,.20))

    def test_short_solid_eyes_survive_a_small_import(self):
        image=Image.new('RGBA',(321,241))
        draw=ImageDraw.Draw(image)
        draw.rectangle((10,20,310,220),fill='#f9d440')
        for x in (95,181):
            draw.rounded_rectangle((x,88,x+17,95),radius=3,fill='#242424')
        buf=io.BytesIO();image.save(buf,'PNG')
        self.uri='data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode()
        made=self.make(20)
        flat=np.array(Image.open(self.root/made['project_id']/made['version_id']/'flat.png'))
        self.assertTrue(np.all(flat[71,92,:3]<80) and np.all(flat[71,178,:3]<80))

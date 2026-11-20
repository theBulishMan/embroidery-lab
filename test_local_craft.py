"""Local craft edits must preserve artwork and reach actual machine files."""
import copy
import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from lxml import etree as ET
from PIL import Image, ImageDraw
from shapely.geometry import LineString, Point, box
from shapely.ops import unary_union

import studio_service as service
from studio_artwork import flatten, STITCH, SVG
from studio_digitize import make_plan
from studio_local import catalog, prepare_regions, guide_coordinates, merge_equivalent_regions


class LocalCraftTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        image=Image.new('RGBA',(300,240));d=ImageDraw.Draw(image)
        d.rounded_rectangle((10,10,210,230),radius=30,fill='#ffda35')
        d.ellipse((75,65,135,125),fill=(0,0,0,0))
        d.rounded_rectangle((245,60,270,160),radius=12,fill='#202020')
        image.save(self.root/'input.png');self.folder=self.root/'art'
        flatten(self.root/'input.png',self.folder,4)
        data=catalog(self.folder,30);self.key=max(data['regions'],key=lambda r:r['area_mm2'])['id']
        self.cut={'region_id':self.key,'points':[[.6,.1],[.6,.9]]}

    def tearDown(self):self.temp.cleanup()

    def test_region_keys_survive_size_change(self):
        self.assertEqual([r['id'] for r in catalog(self.folder,20)['regions']],
                         [r['id'] for r in catalog(self.folder,100)['regions']])

    def test_split_merge_resplit_preserves_color_unions_and_holes(self):
        original=prepare_regions(self.folder,30)[3]
        first=prepare_regions(self.folder,30,[self.cut])[3]
        keys=[r['key'] for r in first if r.get('parent_key')==self.key]
        edits=[self.cut,{'merge_regions':list(reversed(keys))}]
        merged=prepare_regions(self.folder,30,edits)[3]
        whole=next(r for r in merged if r.get('merged_from'))
        source=next(r for r in original if r['key']==self.key)
        self.assertLess(whole['geometry'].symmetric_difference(source['geometry']).area,1e-8)
        self.assertEqual(len(whole['geometry'].interiors),len(source['geometry'].interiors))
        resized=catalog(self.folder,60,edits)
        self.assertIn(whole['key'],[r['id'] for r in resized['regions']])
        edits.append({'region_id':whole['key'],'points':[[.05,.7],[.75,.7]]})
        after=prepare_regions(self.folder,30,edits)[3]
        self.assertGreater(len(after),len(merged))
        for ci in {r['color'] for r in original}:
            a=unary_union([r['geometry'] for r in original if r['color']==ci])
            b=unary_union([r['geometry'] for r in after if r['color']==ci])
            self.assertLess(a.symmetric_difference(b).area,1e-8)

    def test_merge_rejects_other_colors_gaps_and_duplicate_ids(self):
        parts=prepare_regions(self.folder,30,[self.cut])[3]
        kids=[r['key'] for r in parts if r.get('parent_key')==self.key]
        other=next(r['key'] for r in parts if r['key'] not in kids)
        for keys in [[kids[0],other],[kids[0],kids[0]],kids+['missing']]:
            with self.assertRaises(ValueError):prepare_regions(self.folder,30,[self.cut,{'merge_regions':keys}])
        # Two same-color islands stay separate; a merge must not bridge a gap.
        image=Image.new('RGBA',(200,100));d=ImageDraw.Draw(image)
        d.rectangle((5,5,80,95),fill='#222222');d.rectangle((120,5,195,95),fill='#222222')
        path=self.root/'islands.png';image.save(path);folder=self.root/'islands';flatten(path,folder,4)
        ids=[r['id'] for r in catalog(folder,30)['regions']]
        with self.assertRaisesRegex(ValueError,'不相邻'):
            prepare_regions(folder,30,[{'merge_regions':ids}])

    def test_merged_craft_reaches_machine_files_with_unique_object_ids(self):
        first=prepare_regions(self.folder,30,[self.cut])[3]
        child=next(r['key'] for r in first if r.get('parent_key')==self.key)
        edits=[self.cut,{'region_id':child,'points':[[.05,.7],[.75,.7]]}]
        parts=prepare_regions(self.folder,30,edits)[3]
        ids=[r['key'] for r in parts if r.get('parent_key')==child]
        self.assertGreaterEqual(len(ids),2)
        edits.append({'merge_regions':ids})
        combined=prepare_regions(self.folder,30,edits)[3]
        merged=next(r for r in combined if r.get('merged_from'))
        settings={merged['key']:{'method':'fill','angle':37,'fill_spacing':.2}}
        out=self.root/'merged-plan'
        view=make_plan(self.folder,out,30,'75D',.2,satin_spacing=.35,region_cuts=edits,object_overrides=settings)
        self.assertTrue(view['stats']['sew_geometry_dst_pes_equal'])
        items=[o for o in view['meta']['planned_objects'] if o['region_key']==merged['key'] and o['stitch_type']=='填针']
        self.assertTrue(items);self.assertEqual(items[0]['angle'],37)
        xml=ET.parse(str(out/'embroidery.svg'));ids=xml.xpath('//@id')
        self.assertEqual(len(ids),len(set(ids)))
        saved=json.loads((out/'region-settings.json').read_text())
        self.assertEqual(saved['object_overrides'],settings)
        self.assertEqual(len(saved['region_cuts']),3)

    def test_cut_preserves_source_union_holes_and_other_colors(self):
        original=prepare_regions(self.folder,30)[3]
        split=prepare_regions(self.folder,30,[self.cut])[3]
        for color in {r['color'] for r in original}:
            a=unary_union([r['geometry'] for r in original if r['color']==color])
            b=unary_union([r['geometry'] for r in split if r['color']==color])
            self.assertLess(a.symmetric_difference(b).area,1e-8)
        self.assertEqual(sum(len(r['geometry'].interiors) for r in split),1)
        self.assertEqual(len([r for r in split if r.get('parent_key')==self.key]),2)
        with self.assertRaises(ValueError):prepare_regions(self.folder,30,[self.cut],{self.key:{'method':'fill'}})

    def test_invalid_cut_and_curve_do_not_become_fallback_stitches(self):
        for dots in [[[0,0],[0,.9]],[[.5,.5],[.5,.5]]]:
            with self.subTest(dots=dots),self.assertRaises(ValueError):
                prepare_regions(self.folder,30,[{'region_id':self.key,'points':dots}])
        for guide in [[[0,0],[.5,0],[1,0]],[[.4,.5],[.5,.6],[.4,.5]]]:
            with self.subTest(guide=guide),self.assertRaises(ValueError):
                prepare_regions(self.folder,30,overrides={self.key:{'method':'guided','guide':guide}})

    def test_invalid_local_settings_are_rejected(self):
        invalid=[{'method':[]},{'method':'unknown'},{'angle':90},{'method':'fill','angle':float('nan')},
                 {'fill_spacing':.1},{'satin_spacing':1},{'pull_compensation':True},
                 {'underlay':{}},{'method':'guided'},{'method':'running','underlay':'full'},
                 {'unknown_parameter':1}]
        for settings in invalid:
            with self.subTest(settings=settings),self.assertRaises(ValueError):
                prepare_regions(self.folder,30,overrides={self.key:settings})
        with self.assertRaises(ValueError):prepare_regions(self.folder,30,overrides={'c999-r1-s1':{}})

    def test_curve_passes_through_clicked_middle_and_scales_in_mm(self):
        guide=[[.2,.5],[.5,.3],[.8,.5]]
        a=guide_coordinates(guide,30,20);b=guide_coordinates(guide,60,40)
        self.assertEqual(a[32],[20,11])
        for p,q in zip(a,b):
            self.assertAlmostEqual((p[0]-5)*2,q[0]-5)
            self.assertAlmostEqual((p[1]-5)*2,q[1]-5)

    def test_wrong_forced_stitch_is_rejected_before_engine_export(self):
        with patch('studio_digitize.export_engine') as export:
            with self.assertRaises(ValueError):
                make_plan(self.folder,self.root/'bad',30,'75D',.2,
                          object_overrides={self.key:{'method':'running'}})
            export.assert_not_called()

    def test_identical_explicit_craft_removes_internal_seam_but_keeps_editable_regions(self):
        regions=prepare_regions(self.folder,30,[self.cut])[3]
        keys=[r['key'] for r in regions if r.get('parent_key')==self.key]
        self.assertEqual(len(merge_equivalent_regions(regions,{})),len(regions))
        options={key:{'method':'fill','angle':90,'underlay':'full'} for key in keys}
        joined=merge_equivalent_regions(regions,options)
        self.assertEqual(len(joined),len(regions)-1)
        expected=unary_union([r['geometry'] for r in regions if r['key'] in keys])
        actual=next(r for r in joined if len(r['region_keys'])==2)
        self.assertLess(actual['geometry'].symmetric_difference(expected).area,1e-8)
        self.assertEqual(set(actual['region_keys']),set(keys))
        self.assertEqual(len(regions),3)
        options[keys[1]]['angle']=45
        self.assertEqual(len(merge_equivalent_regions(regions,options)),len(regions))

    def test_real_guided_and_split_exports_are_local_and_keep_source(self):
        before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.folder.iterdir() if p.is_file()}
        base=make_plan(self.folder,self.root/'baseline',30,'75D',.2,satin_spacing=.3)
        overrides={self.key:{'method':'guided','guide':[[.1,.7],[.4,.5],[.7,.7]],'underlay':'full','fill_spacing':.22}}
        guided=make_plan(self.folder,self.root/'guided',30,'75D',.2,satin_spacing=.3,object_overrides=overrides)
        self.assertEqual(guided['stats']['stitches_by_color'][1:],base['stats']['stitches_by_color'][1:])
        self.assertNotEqual(guided['stats']['stitches_by_color'][0],base['stats']['stitches_by_color'][0])
        self.assertEqual(guided['stats']['unique_thread_colors'],base['stats']['unique_thread_colors'])
        self.assertTrue(guided['stats']['sew_geometry_dst_pes_equal'])
        self.assertFalse(guided['stats']['physically_sewn'])
        xml=ET.parse(str(self.root/'guided/embroidery.svg'))
        fill=xml.xpath('//*[@inkstitch:fill_method="guided_fill"]',namespaces={'inkstitch':STITCH})
        self.assertEqual(len(fill),1)
        self.assertEqual(len(fill[0].getparent()),2)
        settings=json.loads((self.root/'guided/region-settings.json').read_text())
        self.assertEqual(settings['object_overrides'],overrides)
        with zipfile.ZipFile(self.root/'guided/embroidery-project.zip') as archive:
            self.assertIn('region-settings.json',archive.namelist())
        regions=prepare_regions(self.folder,30,[self.cut])[3]
        children=[r for r in regions if r.get('parent_key')==self.key]
        options={r['key']:{'method':'fill','angle':angle,'underlay':'none'} for r,angle in zip(children,[20,100])}
        split=make_plan(self.folder,self.root/'split',30,'75D',.2,satin_spacing=.3,
                        region_cuts=[self.cut],object_overrides=options)
        actual=[o for o in split['meta']['planned_objects'] if o.get('region_key') in options]
        self.assertEqual(sorted(o['angle'] for o in actual),[20,100])
        self.assertTrue(all(not o['underlay'] and not o['contour_underlay'] for o in actual))
        self.assertTrue(split['meta']['craft']['overlap_objects'])
        # A changed incoming route can move a lock needle; unrelated geometry
        # and craft parameters must stay identical, including the satin rails.
        original_xml=ET.parse(str(self.root/'baseline/embroidery.svg'))
        split_xml=ET.parse(str(self.root/'split/embroidery.svg'))
        for original in original_xml.xpath('//*[@data-thread-color="1"]'):
            updated=split_xml.xpath('//*[@id=$id]',id=original.get('id'))[0]
            for attr,value in original.attrib.items():
                if attr!=f'{{{STITCH}}}trim_after':self.assertEqual(updated.get(attr),value)
        self.assertLessEqual(abs(split['stats']['stitches_by_color'][1]-base['stats']['stitches_by_color'][1]),2)
        self.assertTrue(split['stats']['sew_geometry_dst_pes_equal'])
        self.assertEqual(before,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.folder.iterdir() if p.is_file()})

    def test_plan_history_keys_include_normalized_locals_and_never_call_ai(self):
        project={'id':'local-test','versions':[{'id':'v-test','plans':[]}]}
        captured=[]
        def create(folder,out,width,thread,spacing,update,**options):
            captured.append((out.name,options));out.mkdir(parents=True,exist_ok=True)
            for name in ['design.dst','design.pes','validation.json','embroidery-project.zip']:(out/name).write_text('test')
            (out/'preview.json').write_text(json.dumps({'meta':{'design_height_mm':20},'stats':{'stitch_count':100}}))
        with patch.object(service,'PROJECTS',self.root),patch.object(service,'read_project',return_value=project),\
                patch.object(service,'prepare_regions',side_effect=lambda folder,width,cuts,overrides:prepare_regions(self.folder,width,cuts,overrides)),\
                patch.object(service,'make_plan',side_effect=create),patch.object(service,'save_json'),patch.object(service,'update_job'),\
                patch.object(service,'submit',side_effect=lambda pid,kind,worker,**kw:worker('job')),\
                patch.object(service,'request_image') as provider,patch.object(service,'config') as config:
            body={'version_id':'v-test','width':30,'thread':'75D','spacing':.2,'satin_spacing':.3,'fabric':'twill'}
            a=service.plan_project('local-test',body)
            b=service.plan_project('local-test',{**body,'object_overrides':{self.key:{'method':'fill','angle':90}}})
            c=service.plan_project('local-test',{**body,'object_overrides':{self.key:{'angle':90.0,'method':'fill'}}})
            self.assertNotEqual(a['plan_id'],b['plan_id']);self.assertEqual(b['plan_id'],c['plan_id'])
            self.assertEqual(len(captured),2);self.assertEqual(len(project['versions'][0]['plans']),2)
            self.assertEqual(project['versions'][0]['plans'][0]['object_overrides'],{})
            adaptive=service.plan_project('local-test',{**body,'adaptive_fill':True})
            self.assertNotEqual(a['plan_id'],adaptive['plan_id'])
            self.assertEqual(service.plan_project('local-test',body)['plan_id'],a['plan_id'])
            provider.assert_not_called();config.assert_not_called()


if __name__=='__main__':unittest.main()

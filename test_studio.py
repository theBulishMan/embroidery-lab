"""Regression checks for generic artwork geometry, exports and API boundaries.

These tests use generated geometric fixtures and never call the image provider.
"""
import io
import json
import tempfile
import unittest
import zipfile
import base64
from types import SimpleNamespace
from lxml import etree as ET
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pyembroidery as pe
from PIL import Image, ImageDraw
from shapely.geometry import box,Point
from shapely.affinity import rotate

import studio_service as service
from studio_artwork import flatten, extract_geometries, normalize_image, recolor_artwork, STITCH,SVG,svg_root
from studio_digitize import make_plan, nearby_order, movement_metrics,export_engine,param
from studio_craft import fill_angle,narrow_rails,satin_underlay,satin_short_distance,frame_rails,rail_coverage,covered_stitch_region


class StudioRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def ring_fixture(self):
        image = Image.new('RGBA', (320, 240))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((20, 20, 220, 220), radius=50, fill='#222222')
        draw.rounded_rectangle((35, 35, 205, 205), radius=35, fill=(0, 0, 0, 0))
        draw.ellipse((270, 80, 310, 120), fill='#222222')
        source = self.root / 'ring.png'
        image.save(source)
        return source

    def test_single_color_retains_hole_and_disconnected_island(self):
        folder = self.root / 'art'
        meta, view = flatten(self.ring_fixture(), folder, 9)
        self.assertEqual(len(meta['colors']), 1)
        _, groups, _ = extract_geometries(folder, 60)
        self.assertEqual(len(groups[0]), 2)
        self.assertEqual(sum(len(p.interiors) for p in groups[0]), 1)
        self.assertEqual(len(view['meta']['separated_objects']), 2)
        self.assertTrue(all(p.is_valid for p in groups[0]))
        self.assertTrue((folder / 'layers.svg').is_file())

    def test_palette_ceiling_is_actual_solid_pixel_count(self):
        image = Image.new('RGBA', (400, 200))
        draw = ImageDraw.Draw(image)
        colors = ['#ec4770', '#3980e8', '#50bd74', '#f4c453', '#a273dd',
                  '#e47235', '#67d5d7', '#402e3e', '#f6f6f3', '#9b2525']
        for i, color in enumerate(colors):
            x, y = (i % 5) * 80, (i // 5) * 100
            draw.rounded_rectangle((x + 5, y + 5, x + 75, y + 95), radius=12, fill=color)
        source = self.root / 'colors.png'
        image.save(source)
        folder = self.root / 'limited'
        meta, view = flatten(source, folder, 4)
        pixels = np.asarray(Image.open(folder / 'flat.png'))
        actual = np.unique(pixels[pixels[:, :, 3] == 255, :3], axis=0)
        self.assertEqual(len(actual), len(meta['colors']))
        self.assertLessEqual(len(actual), 4)
        self.assertTrue(set(np.unique(pixels[:, :, 3])).issubset({0, 255}))
        self.assertEqual(len(view['meta']['separated_objects']), 10)

    def test_opaque_background_does_not_remove_enclosed_white_feature(self):
        image = Image.new('RGB', (160, 160), 'white')
        draw = ImageDraw.Draw(image)
        draw.ellipse((20, 20, 140, 140), fill='#202020')
        draw.ellipse((60, 60, 100, 100), fill='white')
        source = self.root / 'opaque.png'
        image.save(source)
        folder = self.root / 'opaque'
        meta, _ = flatten(source, folder, 9)
        result = np.asarray(Image.open(folder / 'flat.png'))
        self.assertEqual(len(meta['colors']), 2)
        self.assertEqual(int(result[60, 60, 3]), 255)
        self.assertEqual(int(result[0, 0, 3]), 0)

    def test_export_uses_generic_single_color_geometry_and_real_counts(self):
        folder = self.root / 'art'
        flatten(self.ring_fixture(), folder, 9)
        out = self.root / 'plan'
        view = make_plan(folder, out, 60, '105D', .22, satin_spacing=.40)
        stats = view['stats']
        self.assertEqual(stats['unique_thread_colors'], 1)
        self.assertEqual(stats['color_changes'], 0)
        self.assertGreater(stats['stitch_count'], 0)
        self.assertEqual(sum(stats['stitches_by_color']), stats['stitch_count'])
        self.assertEqual(view['prefix'][-1], stats['stitch_count'])
        self.assertTrue(stats['sew_geometry_dst_pes_equal'])
        self.assertFalse(stats['physically_sewn'])
        self.assertLessEqual(stats['max_stitch_mm'], 6.1)
        for name in ['design.dst', 'design.pes']:
            pattern = pe.read(str(out / name))
            self.assertEqual(sum(c & 255 == pe.STITCH for _, _, c in pattern.stitches), stats['stitch_count'])
        with zipfile.ZipFile(out / 'embroidery-project.zip') as archive:
            self.assertIsNone(archive.testzip())
            self.assertTrue({'embroidery.svg', 'dimensions.svg', 'design.dst', 'design.pes', 'threads.csv'}.issubset(archive.namelist()))
        self.assertIn('60 mm', (out / 'dimensions.svg').read_text())
        paths=ET.parse(str(out/'embroidery.svg')).getroot()
        satins=[p for p in paths if p.get(f'{{{STITCH}}}satin_column')=='true']
        self.assertTrue(satins)
        self.assertTrue(all(p.get(f'{{{STITCH}}}zigzag_spacing_mm')=='0.4' for p in satins))

    def test_denser_fill_adds_real_needles_and_crosses_underlay(self):
        source=self.root/'density.png';image=Image.new('RGBA',(200,200));draw=ImageDraw.Draw(image)
        draw.rectangle((10,10,190,90),fill='#E3B543');draw.rectangle((10,110,190,190),fill='#222222');image.save(source)
        folder=self.root/'density';flatten(source,folder,4)
        coarse=make_plan(folder,self.root/'coarse',30,'105D',.42,satin_spacing=.40)
        dense=make_plan(folder,self.root/'dense',30,'105D',.22,satin_spacing=.40)
        self.assertGreater(dense['stats']['stitch_count'],coarse['stats']['stitch_count']*1.4)
        self.assertEqual(dense['meta']['preview_thread_width_mm'],coarse['meta']['preview_thread_width_mm'])
        self.assertEqual(dense['meta']['separated_objects'],coarse['meta']['separated_objects'])
        self.assertEqual(dense['meta']['export_origin_mm'],coarse['meta']['export_origin_mm'])
        # With a shared artwork origin, row endpoints quantise onto the same
        # 0.1 mm machine grid rather than each design's independently centred
        # grid. Different row phases may differ by one machine unit in bounds.
        for a,b in zip(dense['stats']['dimensions_mm'],coarse['stats']['dimensions_mm']):
            self.assertAlmostEqual(a,b,delta=.101)
        root=ET.parse(str(self.root/'dense'/'embroidery.svg')).getroot()
        fills=[p for p in root if p.get(f'{{{STITCH}}}fill_underlay')=='true']
        self.assertEqual(len(fills),2)
        for p in fills:
            get=lambda key:p.get(f'{{{STITCH}}}{key}')
            self.assertEqual((float(get('fill_underlay_angle'))-float(get('angle')))%180,90)
            self.assertEqual(float(get('fill_underlay_row_spacing_mm')),.66)
        for name in ['design.dst','design.pes']:
            pattern=pe.read(str(self.root/'dense'/name))
            self.assertEqual(sum(c&255==pe.STITCH for _,_,c in pattern.stitches),dense['stats']['stitch_count'])

    def test_nearby_routing_keeps_overlaps_colors_and_outline_last(self):
        shapes=[box(0,0,4,4),box(30,0,34,4),box(0,6,4,10),box(30,6,34,10)]
        shapes.append(box(-2,-2,36,12).difference(box(-1,-1,35,11)))
        bundles=[{'id':str(i),'color':0,'geometry':p,'outline':i==4} for i,p in enumerate(shapes)]
        bundles.append({'id':'next-color','color':1,'geometry':box(5,0,8,3),'outline':False})
        order=nearby_order(bundles)
        self.assertEqual([b['id'] for b in order],['0','2','3','1','4','next-color'])
        # Slightly separated geometry may still overlap after compensation;
        # preserve the original stacking order of such objects.
        overlap=[{'id':str(i),'color':0,'geometry':p,'outline':False} for i,p in enumerate([box(0,0,2,2),box(4,0,6,2),box(2.1,0,3.9,2)])]
        self.assertEqual([b['id'] for b in nearby_order(overlap)],['0','1','2'])

    def test_routing_is_measured_from_machine_file_and_retains_all_colors(self):
        source=self.root/'routing.png';image=Image.new('RGBA',(360,220));draw=ImageDraw.Draw(image)
        for x in [10,290]:
            for y in [10,85,160]:draw.ellipse((x,y,x+55,y+45),fill='#dd6577')
        draw.rectangle((130,80,220,135),fill='#efd45e');image.save(source)
        folder=self.root/'routing';flatten(source,folder,4);out=self.root/'routed'
        view=make_plan(folder,out,60,'105D',.22,satin_spacing=.4)
        stats=view['stats'];metrics=movement_metrics(pe.read(str(out/'design.dst')))
        self.assertEqual(metrics['travel_mm'],stats['travel_mm'])
        self.assertLessEqual(stats['travel_mm'],stats['travel_before_mm'])
        self.assertEqual(stats['color_changes'],stats['unique_thread_colors']-1)
        runs=[]
        for _,_,command,color in view['commands']:
            if command==pe.STITCH and (not runs or runs[-1]!=color):runs.append(color)
        self.assertEqual(runs,list(range(stats['unique_thread_colors'])))
        self.assertEqual(len(view['meta']['routing']['object_order']),len(view['meta']['planned_objects']))

    def test_geometry_direction_and_width_choose_stitch_construction(self):
        strip=box(0,0,8,1.2)
        self.assertEqual(fill_angle(strip),0)
        self.assertEqual(fill_angle(rotate(strip,45)),135)
        self.assertIsNotNone(narrow_rails(strip))
        self.assertIsNone(narrow_rails(box(0,0,12,8)))
        self.assertIsNotNone(narrow_rails(Point(0,0).buffer(.75)))
        fork=box(0,0,1,10).union(box(3,0,4,10)).union(box(0,0,4,1))
        self.assertIsNone(narrow_rails(fork))
        self.assertTrue(satin_underlay(1.5)['center_walk_underlay'])
        self.assertFalse(satin_underlay(1.5)['zigzag_underlay'])
        self.assertTrue(satin_underlay(2.5)['contour_underlay'])
        self.assertTrue(satin_underlay(4.5)['zigzag_underlay'])
        self.assertTrue(satin_underlay(1.8,2.8)['contour_underlay'])

    def test_dense_fill_retains_full_width_row_turns_in_machine_file(self):
        # A row-count/density test alone misses deleted endpoints. Inspect the
        # real DST: at least 95% of rows must reach BOTH sides of this square.
        from collections import defaultdict
        root=svg_root(20,20)
        node=ET.SubElement(root,f'{{{SVG}}}path',d='M5,5 L15,5 L15,15 L5,15 Z',fill='#ffd500')
        for key,val in {'auto_fill':True,'angle':0,'row_spacing_mm':.2,
                        'fill_underlay':False,'skip_last':False,'max_stitch_length_mm':3.2}.items():param(node,key,val)
        dst,_=export_engine(root,self.root/'row-turns')
        rows=defaultdict(list)
        for x,y,command in dst.stitches:
            if command&255==pe.STITCH:rows[round(y/10,2)].append(x/10)
        rows=[xs for xs in rows.values() if len(xs)>=3]
        full=sum(min(xs)<=-4.9 and max(xs)>=4.9 for xs in rows)
        self.assertGreaterEqual(len(rows),50)
        self.assertGreaterEqual(full/len(rows),.95)

    def test_fine_satin_keeps_straight_rail_endpoints_in_machine_file(self):
        # With a fixed 0.25 mm shortening threshold, a 0.20 mm satin recesses
        # alternate pairs on BOTH straight rails. Inspect real exported points.
        root=svg_root(20,20)
        node=ET.SubElement(root,f'{{{SVG}}}path',d='M5,9.4 L15,9.4 M5,10.6 L15,10.6',fill='none',stroke='#171717')
        for key,val in {'satin_column':True,'zigzag_spacing_mm':.20,
                        'short_stitch_inset':15,'short_stitch_distance_mm':satin_short_distance(.20),
                        'center_walk_underlay':False,'contour_underlay':False,'zigzag_underlay':False,
                        'pull_compensation_mm':0,'max_stitch_length_mm':5.8}.items():param(node,key,val)
        dst,_=export_engine(root,self.root/'fine-satin')
        interior=[(x/10,y/10) for x,y,c in dst.stitches if c&255==pe.STITCH and abs(x/10)<4.5]
        self.assertGreater(len(interior),70)
        self.assertGreater(sum(abs(y)>=.55 for _,y in interior)/len(interior),.95)
        self.assertEqual(service.checked_settings({'thread':'75D','satin_spacing':.20})['satin_spacing'],.20)

    def test_underlap_fills_only_small_later_covered_details(self):
        eye=Point(5,5).buffer(.8);open_hole=Point(8,8).buffer(.7)
        base=box(0,0,12,12).difference(eye.union(open_hole))
        frame=box(-1,-1,13,13).difference(box(0,0,12,12))
        result,details=covered_stitch_region(base,[eye,frame])
        self.assertEqual(details['closed_detail_holes'],1)
        self.assertLess(eye.difference(result).area,.001)
        self.assertLess(result.intersection(open_hole).area,.001)
        self.assertGreater(result.intersection(frame).area,1)
        self.assertLess(result.difference(base.buffer(.18).union(eye)).area,.001)
        # No later cover: no expansion of an exposed edge or transparent hole.
        unchanged,details=covered_stitch_region(base,[])
        self.assertTrue(unchanged.equals(base))
        large=Point(6,6).buffer(2)
        result,details=covered_stitch_region(box(0,0,12,12).difference(large),[large])
        self.assertEqual(details['closed_detail_holes'],0)
        self.assertFalse(result.contains(Point(6,6)))

    def test_fabric_changes_support_and_compensation_without_changing_selected_density(self):
        folder,_,_=self.two_color_fixture()
        views={}
        for fabric in ['twill','knit']:
            out=self.root/fabric
            views[fabric]=make_plan(folder,out,30,'75D',.20,satin_spacing=.35,fabric=fabric)
            paths=ET.parse(str(out/'embroidery.svg')).getroot()
            fills=[p for p in paths if p.get(f'{{{STITCH}}}fill_underlay')=='true']
            self.assertTrue(fills)
            for p in fills:
                self.assertEqual(float(p.get(f'{{{STITCH}}}row_spacing_mm')),.20)
                self.assertEqual(float(p.get(f'{{{STITCH}}}pull_compensation_mm')),.12 if fabric=='twill' else .20)
            self.assertEqual(views[fabric]['meta']['fabric'],fabric)
        self.assertGreater(views['knit']['meta']['craft']['underlap_mm'],views['twill']['meta']['craft']['underlap_mm'])
        self.assertTrue(satin_underlay(2.8,3.0,True)['zigzag_underlay'])
        self.assertFalse(satin_underlay(2.8,3.0,False)['zigzag_underlay'])
        for fabric in ['unknown',[],None]:
            with self.assertRaises(ValueError):service.checked_settings({'thread':'75D','fabric':fabric})

    def test_adaptive_fill_preserves_explicit_local_pitch(self):
        folder,_,_=self.two_color_fixture()
        normal=make_plan(folder,self.root/'broad-auto',30,'75D',.20,adaptive_fill=True)
        fills=[o for o in normal['meta']['planned_objects'] if 'row_spacing_mm' in o]
        adjusted=next(o for o in fills if o['row_spacing_mm']==.18)
        override={adjusted['region_key']:{'method':'fill','fill_spacing':.20}}
        edited=make_plan(folder,self.root/'local-fixed',30,'75D',.20,adaptive_fill=True,object_overrides=override)
        local=[o for o in edited['meta']['planned_objects'] if o['region_key']==adjusted['region_key'] and 'row_spacing_mm' in o]
        self.assertEqual([o['row_spacing_mm'] for o in local],[.20])

    def test_craft_export_sews_highlights_before_final_border_with_real_color_returns(self):
        image=Image.new('RGBA',(260,260));draw=ImageDraw.Draw(image)
        draw.ellipse((5,5,255,255),fill='#293e19')
        draw.ellipse((17,17,243,243),fill='#f4f3ef')
        for x in [70,160]:
            draw.ellipse((x,75,x+35,125),fill='#242125')
            # A 1.4 mm highlight at this physical size, inside the eye hole.
            draw.ellipse((x+12,85,x+18,91),fill='#f4f3ef')
        draw.rounded_rectangle((70,160,185,175),radius=7,fill='#df7996')
        source=self.root/'face.png';image.save(source);folder=self.root/'face';flatten(source,folder,5)
        out=self.root/'craft';view=make_plan(folder,out,50,'105D',.22,satin_spacing=.4)
        self.assertTrue(view['meta']['craft']['border_last'])
        steps=view['meta']['sewing_steps'];self.assertEqual(steps[-1]['stage'],'border')
        self.assertTrue(any(s['stage']=='highlight' for s in steps))
        colors=[s['color'] for s in steps]
        self.assertGreater(len(colors),len(set(colors)))
        self.assertEqual(view['stats']['color_changes'],len(steps)-1)
        self.assertEqual(sum(s['stitch_count'] for s in steps),view['stats']['stitch_count'])
        root=ET.parse(str(out/'embroidery.svg')).getroot()
        paths=[p for p in root if p.get('id')]
        self.assertEqual(paths[-1].get('data-role'),'最后包边')
        for i,node in enumerate(paths):
            if node.get('data-role')=='轮廓底针':
                fill=paths[i+1]
                self.assertEqual(fill.get('id'),node.get('id').removesuffix('-underlay'))
                get=lambda key:fill.get(f'{{{STITCH}}}{key}')
                self.assertEqual(get('fill_underlay'),'true')
                self.assertEqual(get('fill_underlay_inset_mm'),'0.4')
                self.assertEqual(get('staggers'),'4')
                self.assertEqual((float(get('fill_underlay_angle'))-float(get('angle')))%180,90)
        import csv
        with (out/'threads.csv').open(encoding='utf-8-sig') as source:
            rows=list(csv.reader(source))
        self.assertEqual(len(rows)-1,len(steps))

    def test_multi_window_border_does_not_stitch_across_its_holes(self):
        frame=box(0,0,20,20).difference(box(1,1,19,9).union(box(1,11,19,19)))
        sections=frame_rails(frame)
        self.assertGreaterEqual(len(sections),2)
        for aa,bb,_ in sections:
            self.assertLess(rail_coverage(aa,bb).difference(frame.buffer(.08)).area,.02)
        image=Image.new('RGBA',(260,260));draw=ImageDraw.Draw(image)
        draw.rounded_rectangle((5,5,255,255),radius=30,fill='#191919')
        draw.rounded_rectangle((20,20,240,120),radius=20,fill='#f9d51c')
        draw.rounded_rectangle((20,140,240,240),radius=20,fill='#ffffff')
        source=self.root/'windows.png';image.save(source);folder=self.root/'windows';flatten(source,folder,3)
        view=make_plan(folder,self.root/'windows-plan',20,'75D',.20,satin_spacing=.35)
        borders=[o for o in view['meta']['planned_objects'] if o.get('role')=='最后包边']
        self.assertGreaterEqual(len(borders),2)
        self.assertTrue(all(o['stitch_type']=='缎面针' for o in borders))
        self.assertEqual(view['meta']['sewing_steps'][-1]['stage'],'border')
        self.assertEqual(view['stats']['unique_thread_colors'],3)
        self.assertTrue(view['stats']['sew_geometry_dst_pes_equal'])

    def test_invalid_inputs_do_not_reach_generation(self):
        with patch.object(service, 'PROJECTS', self.root), patch.object(service, 'generate_version') as generate:
            for body in [
                {'mode': 'text', 'prompt': '', 'max_colors': 4},
                {'mode': 'text', 'prompt': '小猫', 'max_colors': 10},
                {'mode': 'text', 'prompt': '小猫', 'max_colors': True},
                {'mode': 'upload', 'prompt': '', 'image_data': 'data:text/plain;base64,AA==', 'max_colors': 4},
            ]:
                with self.assertRaises(ValueError):
                    service.new_project(body)
            generate.assert_not_called()
        with self.assertRaisesRegex(ValueError, '图片无法读取'):
            normalize_image(io.BytesIO(b'not an image'), self.root / 'invalid.png')

    def test_phone_jpeg_with_auxiliary_mpo_frame_uses_primary_photo(self):
        first=Image.new('RGB',(40,30),'#ee3322');second=Image.new('RGB',(40,30),'#2233ee')
        source=self.root/'phone.jpg';first.save(source,format='MPO',save_all=True,append_images=[second])
        out=self.root/'phone.png';normalize_image(source,out)
        with Image.open(out) as image:
            self.assertEqual(image.size,(40,30))
            self.assertGreater(image.getpixel((20,15))[0],200)
            self.assertLess(image.getpixel((20,15))[2],80)

    def test_edit_keeps_parent_selection_and_style_rules(self):
        project = {'id': 'test', 'versions': [{'id': 'v-first', 'max_colors': 4}]}
        with patch.object(service, 'read_project', return_value=project), patch.object(service, 'generate_version') as generate:
            service.edit_project('test', {'parent_version_id': 'v-first', 'prompt': '把腮红改成粉色'})
            generate.assert_called_once_with('test', 'edit', '把腮红改成粉色', 4, 'v-first', detail_mode='preserve')
            service.edit_project('test', {'parent_version_id':'v-first','prompt':'主动简化图案','detail_mode':'simplify'})
            self.assertEqual(generate.call_args.kwargs['detail_mode'],'simplify')
        for mode in ['upload', 'edit']:
            prompt = service.style_prompt('增加一顶帽子', 4, mode)
            self.assertIn('大花、几何图腾、花瓣分区、条纹等必须保留', prompt)
            self.assertIn('合并相近颜色', prompt)
            self.assertIn('最多4种必要纯色', prompt)
            self.assertIn('不能声称已通过实绣', prompt)

    def two_color_fixture(self):
        image=Image.new('RGBA',(160,160))
        draw=ImageDraw.Draw(image)
        draw.rounded_rectangle((10,10,145,145),radius=25,fill='#e5b54a')
        draw.ellipse((50,50,110,110),fill='#255780')
        source=self.root/'two.png';image.save(source)
        folder=self.root/'two';meta,view=flatten(source,folder,4)
        return folder,meta,view

    def test_recolor_preserves_shapes_pixels_and_parent_files(self):
        folder,meta,view=self.two_color_fixture()
        original={p.name:p.read_bytes() for p in folder.iterdir()}
        changed=['#70B8CF',meta['colors'][1]['hex']]
        out=self.root/'changed';new_meta,new_view=recolor_artwork(folder,out,changed)
        np.testing.assert_array_equal(np.load(folder/'classes.npy'),np.load(out/'classes.npy'))
        self.assertEqual(view['meta']['separated_objects'],new_view['meta']['separated_objects'])
        before=np.array(Image.open(folder/'flat.png'));after=np.array(Image.open(out/'flat.png'))
        classes=np.load(folder/'classes.npy')
        np.testing.assert_array_equal(before[:,:,3],after[:,:,3])
        np.testing.assert_array_equal(before[classes!=0],after[classes!=0])
        self.assertTrue(np.all(after[classes==0,:3]==[112,184,207]))
        self.assertEqual(new_meta['colors'][0]['hex'],'#70B8CF')
        self.assertTrue(new_meta['colors'][0]['thread']['code'])
        for name,data in original.items():self.assertEqual((folder/name).read_bytes(),data)

    def test_equal_colors_merge_without_changing_outer_mask(self):
        folder,_,_=self.two_color_fixture();out=self.root/'merged'
        meta,view=recolor_artwork(folder,out,['#60A0D0','#60a0d0'])
        self.assertEqual(len(meta['colors']),1)
        self.assertEqual(len(view['meta']['separated_objects']),1)
        np.testing.assert_array_equal(np.load(folder/'classes.npy')!=255,np.load(out/'classes.npy')!=255)

    def test_palette_endpoint_uses_no_ai_and_keeps_source_settings(self):
        folder,meta,_=self.two_color_fixture()
        projects=self.root/'projects';target=projects/'test'/'v-parent';target.parent.mkdir(parents=True)
        import shutil
        shutil.copytree(folder,target)
        parent={'id':'v-parent','max_colors':4,'aspect_ratio':1,'plans':[{'width':42,'thread':'75D','spacing':.35}]}
        project={'id':'test','versions':[parent]};(target.parent/'project.json').write_text(json.dumps(project))
        changes=['#70B8CF',meta['colors'][1]['hex']]
        def direct_submit(pid,kind,worker):
            self.assertEqual(kind,'palette')
            return worker('test-job')
        with patch.object(service,'PROJECTS',projects),patch.object(service,'submit',side_effect=direct_submit),patch.object(service,'update_job'),patch.object(service,'request_image') as provider:
            result=service.recolor_project('test',{'parent_version_id':'v-parent','colors':changes})
            saved=service.read_project('test');new=saved['versions'][-1]
            self.assertEqual(new['parent_id'],'v-parent')
            self.assertEqual(new['preferred_settings'],{'width':42,'thread':'75D','spacing':.35})
            self.assertEqual(new['plans'],[])
            self.assertEqual(new['id'],result['version_id'])
            self.assertEqual(saved['versions'][0],parent)
            provider.assert_not_called()
            service.recolor_project('test',{'parent_version_id':'v-parent','colors':changes,'settings':{'width':55,'thread':'105D','spacing':.44,'fabric':'knit'}})
            self.assertEqual(service.read_project('test')['versions'][-1]['preferred_settings'],{'width':55,'thread':'105D','spacing':.44,'satin_spacing':.40,'fabric':'knit'})
            with self.assertRaises(ValueError):service.recolor_project('test',{'parent_version_id':'v-parent','colors':['javascript:bad','#FFFFFF']})

    def test_ai_edit_inherits_only_design_settings(self):
        folder,_,_=self.two_color_fixture();projects=self.root/'projects';target=projects/'test'/'v-parent';target.parent.mkdir(parents=True)
        import shutil
        shutil.copytree(folder,target)
        parent={'id':'v-parent','max_colors':4,'plans':[{'width':42,'thread':'75D','spacing':.20,'satin_spacing':.35}]}
        (target.parent/'project.json').write_text(json.dumps({'id':'test','versions':[parent]}))
        result=((folder/'flat.png').read_bytes(),{'generation_seconds':1.0})
        secret={'OPENAI_API_KEY':'test-provider-secret','OPENAI_BASE_URL':'https://example.invalid/v1'}
        with patch.object(service,'PROJECTS',projects),patch.object(service,'submit',side_effect=lambda p,k,w:w('test-job')),patch.object(service,'update_job'),patch.object(service,'config',return_value=secret),patch.object(service,'request_image') as provider:
            provider.return_value=result
            service.generate_version('test','edit','只改颜色',4,'v-parent')
            new=service.read_project('test')['versions'][-1]
            self.assertEqual(new['preferred_settings'],parent['plans'][0])
            self.assertNotIn('test-provider-secret',(target.parent/'project.json').read_text())
            explicit={'width':55,'thread':'105D','spacing':.22,'satin_spacing':.40}
            service.generate_version('test','edit','只改颜色',4,'v-parent',settings=explicit)
            self.assertEqual(service.read_project('test')['versions'][-1]['preferred_settings'],explicit)

    def test_density_validation_accepts_independent_spacing_and_rejects_nonfinite(self):
        self.assertEqual(service.checked_settings({'thread':'75D'})['spacing'],.20)
        self.assertEqual(service.checked_settings({'thread':'105D'})['satin_spacing'],.40)
        for key,value in [('width',float('nan')),('spacing',float('inf')),('spacing',.1),('satin_spacing',.1)]:
            with self.assertRaises(ValueError):service.checked_settings({'thread':'105D',key:value})


if __name__ == '__main__':
    unittest.main()

"""Geometry and real-file checks for turning satin and covered connections."""
import tempfile
import unittest
import base64
import io
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pyembroidery as pe
from lxml import etree as ET
from PIL import Image, ImageDraw
from shapely.geometry import LineString, Point, box

from studio_artwork import SVG, STITCH, svg_root
from studio_craft import curved_rails, frame_rails, rail_coverage, rail_quality
from studio_digitize import ENGINE, export_engine, param
from studio_routing import hidden_route, same_color_short_route, connection_candidate
import studio_service as service


class CraftV8Tests(unittest.TestCase):
    def test_flat_import_never_constructs_an_ai_client_or_reads_api_configuration(self):
        image=Image.new('RGBA',(160,160));draw=ImageDraw.Draw(image)
        draw.ellipse((10,10,150,150),fill='#ffdd22');draw.ellipse((55,50,70,70),fill='#151515')
        data=io.BytesIO();image.save(data,'PNG')
        with tempfile.TemporaryDirectory() as temp,patch.object(service,'PROJECTS',Path(temp)),\
                patch.object(service,'submit',side_effect=lambda p,k,w:w('test-job')),\
                patch.object(service,'update_job'),patch.object(service,'request_image') as provider,\
                patch.object(service,'config') as config:
            result=service.new_project({'mode':'flat','prompt':'直接导入测试','max_colors':4,
                'image_data':'data:image/png;base64,'+base64.b64encode(data.getvalue()).decode()})
            project=service.read_project(result['project_id']);version=project['versions'][0]
            self.assertEqual(version['kind'],'flat')
            self.assertEqual(version['color_count'],2)
            self.assertEqual(version['plans'],[])
            self.assertEqual(version['id'],result['version_id'])
            self.assertTrue((Path(temp)/project['id']/version['id']/'layers.svg').is_file())
            provider.assert_not_called();config.assert_not_called()

    def test_flat_import_rejects_missing_image_before_creating_project(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(service,'PROJECTS',Path(temp)),\
                patch.object(service,'request_image') as provider:
            with self.assertRaises(ValueError):service.new_project({'mode':'flat','max_colors':4})
            self.assertEqual(list(Path(temp).iterdir()),[])
            provider.assert_not_called()

    def test_turning_satin_follows_u_curve_without_crossing_or_losing_shape(self):
        p=LineString([(8*np.cos(a),8*np.sin(a)) for a in np.linspace(0,np.pi,90)]).buffer(.7)
        rails=curved_rails(p)
        self.assertIsNotNone(rails)
        a,b,width=rails
        coverage=rail_coverage(a,b)
        self.assertLess(p.difference(coverage.buffer(.06)).area,p.area*.025)
        self.assertLess(coverage.difference(p.buffer(.06)).area,.02)
        direction=np.asarray(b)-np.asarray(a)
        self.assertGreater(np.ptp(np.unwrap(np.arctan2(direction[:,1],direction[:,0]))),2.5)
        rungs=[LineString([x,y]) for x,y in zip(a,b)]
        self.assertFalse(any(x.crosses(y) for i,x in enumerate(rungs) for y in rungs[i+1:]))
        self.assertEqual(rail_quality(a,b)['stationary_steps'],0)

    def test_branch_wide_region_and_transparent_hole_are_not_one_curved_column(self):
        fork=box(4,0,5,10).union(box(0,8,9,9))
        for shape in [fork,box(0,0,12,8),Point(0,0).buffer(5).difference(Point(0,0).buffer(4))]:
            self.assertIsNone(curved_rails(shape))

    def test_frame_stays_inside_original_shape_and_preserves_holes(self):
        outer=box(0,0,20,20).buffer(2,join_style=1)
        holes=box(1,1,19,9).union(box(1,11,19,19))
        frame=outer.difference(holes)
        sections=frame_rails(frame)
        self.assertIsNotNone(sections)
        for a,b,width in sections:
            coverage=rail_coverage(a,b)
            self.assertLess(coverage.difference(frame.buffer(.08)).area,.02)
            self.assertLess(coverage.intersection(holes.buffer(-.08)).area,.02)

    def test_hidden_route_goes_around_a_hole_and_remains_under_future_fill(self):
        cover=box(0,0,10,8).difference(box(3,2,7,6))
        path=hidden_route([1,4],[9,4],cover,box(0,3,2,5))
        self.assertIsNotNone(path)
        line=LineString(path)
        self.assertGreater(line.length,8)
        self.assertTrue(cover.buffer(-.055).covers(line))

    def test_exposed_gap_and_long_previously_sewn_lead_keep_trim(self):
        self.assertIsNone(hidden_route([1,1],[9,1],box(8,0,10,2),box(0,0,2,2)))
        cover=box(0,0,2,2).union(box(8,0,10,2))
        self.assertIsNone(hidden_route([1,1],[9,1],cover,box(0,0,2,2)))
        self.assertIsNone(hidden_route([1,1],[9,1],box(5,0,10,2),box(0,0,5,2)))

    def test_short_join_cannot_cross_the_gap_between_two_eyes(self):
        eyes=Point(0,0).buffer(.65).union(Point(1.8,0).buffer(.65))
        self.assertIsNone(same_color_short_route([.64,0],[1.16,0],eyes))
        self.assertIsNotNone(same_color_short_route([.1,.5],[1.1,.5],box(0,0,2,1)))
        self.assertIsNone(same_color_short_route([.1,.5],[3.1,.5],box(0,0,4,1)))

    def test_editable_connection_reduces_real_trims_and_exports_equal_formats(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);root=svg_root(20,15)
            shapes={'first':box(5,5,7,7),'second':box(6,5,15,7)}
            for key,p in shapes.items():
                path='M'+' L'.join(f'{x},{y}' for x,y in p.exterior.coords)+' Z'
                node=ET.SubElement(root,f'{{{SVG}}}path',id=key,d=path,fill='#CC3366',
                                   **{'data-thread-color':'0','data-stage':'body'})
                for k,v in {'auto_fill':True,'angle':0,'row_spacing_mm':.3,
                            'fill_underlay':False,'trim_after':True,'max_stitch_length_mm':3}.items():param(node,k,v)
            original,_=export_engine(root,folder/'original')
            candidate,proposals=connection_candidate(root,shapes,ENGINE,folder/'routes')
            self.assertEqual(len(proposals),1)
            result,pes=export_engine(candidate,folder/'connected')
            trims=lambda p:sum(c&255==pe.TRIM for _,_,c in p.stitches)
            self.assertLess(trims(result),trims(original))
            self.assertEqual(sum(c&255==pe.COLOR_CHANGE for _,_,c in result.stitches),0)
            # Unmodified artwork and unchanged order; only trim and connector differ.
            originals={n.get('id'):n for n in root}
            for node in candidate:
                if node.get('id') in originals:
                    self.assertEqual(node.get('d'),originals[node.get('id')].get('d'))
            self.assertEqual(len([n for n in candidate if n.get('data-role') in ['同色藏线','同色短接']]),sum(p['emits_path'] for p in proposals))
            self.assertEqual(originals['first'].get(f'{{{STITCH}}}trim_after'),'true')


if __name__=='__main__':unittest.main()

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image
from shapely.geometry import LineString, Point, box
from shapely.strtree import STRtree

import studio_artwork as art
from studio_craft import fine_line, inlaid_satin_band, inset_details, machine_fill_geometry, recommend_stitch
from studio_digitize import make_plan,verified_passes
from studio_routing import order_visible_layers
from lxml import etree as ET
from studio_fit import fit_artwork
from studio_quality import review_needle_geometry,bridged_gaps
from studio_separation import separate_colors


class VectorToStitchTests(unittest.TestCase):
    def test_size_fit_never_retraces_a_confirmed_outline_when_no_pixels_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data=np.zeros((141,161,4),np.uint8)
            cv2.circle(data,(80,70),61,(80,100,140,255),-1)
            cv2.circle(data,(80,70),21,(240,210,98,255),-1)
            Image.fromarray(data).save(root/'sample.png')
            art.flatten(root/'sample.png',root/'source',4,detail_mode='simplify',width=40,thread='75D')
            before=art.artwork_view(root/'source',40,'75D')['meta']['separated_objects']
            _,view,report=fit_artwork(root/'source',root/'fit',40,'75D')
            self.assertEqual(report['changes'],[])
            self.assertEqual(view['meta']['separated_objects'],before)

    def test_only_supported_two_sided_ribbons_receive_inlaid_edge_treatment(self):
        left = [(0., 0.), (8., 0.)]
        right = [(0., 1.), (8., 1.)]
        lower, middle, upper = box(0,-1,8,0), box(0,0,8,1), box(0,1,8,2)
        def eligible(shapes, colors, position):
            return inlaid_satin_band(left, right, [1.,1.], .20, STRtree(shapes),
                                     np.asarray(colors), position)
        self.assertTrue(eligible([lower, upper, middle], [0,2,1], 2))
        self.assertFalse(eligible([lower, middle, upper], [0,1,2], 1))
        self.assertFalse(eligible([lower, upper, middle], [1,2,1], 2))
        self.assertFalse(eligible([lower, box(0,1,1.5,2), middle], [0,2,1], 2))

    def test_same_colour_petals_must_keep_the_intervening_gap(self):
        regions=[{'key':'p1','color':0,'geometry':box(1,1,2,2)},{'key':'p2','color':0,'geometry':box(2.2,1,3.2,2)}]
        expected=np.zeros((80,100),np.uint8);expected[20:41,20:41]=1;expected[20:41,44:65]=1
        self.assertFalse(bridged_gaps(regions,expected,expected,.05,.20))
        joined=expected.copy();joined[20:41,40:45]=1
        gap,=bridged_gaps(regions,expected,joined,.05,.20)
        self.assertEqual((gap['first'],gap['second'],gap['gap_mm']),('p1','p2',.20))

    def test_enclosing_color_precedes_inlay_even_if_ai_suggests_reverse(self):
        inlay=Point(5,5).buffer(1);host=box(0,0,10,10).difference(inlay)
        objects=[({'key':'petal','geometry':inlay,'color':0},'body'),
                 ({'key':'wing','geometry':host,'color':1},'body'),
                 ({'key':'edge','geometry':box(11,2,12,3),'color':0},'border')]
        ordered=order_visible_layers(objects,set(),.1,{'petal':'base','wing':'detail','edge':'base'})
        self.assertEqual([r['key'] for r,_ in ordered],['wing','petal','edge'])

    def test_thread_list_only_omits_subresolution_color_stops(self):
        root=ET.Element('svg');colors=[{'hex':'#112233'},{'hex':'#eeeeee'}]
        for key,ci in [('body',0),('tiny',1),('border',0)]:
            ET.SubElement(root,'path',attrib={'data-thread-color':str(ci),'data-region-key':key})
        emitted='1 None #None - None (#112233)'
        passes,omitted=verified_passes(root,emitted,colors,{'body':8,'tiny':.01,'border':3})
        self.assertEqual(omitted,['tiny']);self.assertEqual(passes[0]['keys'],{'body','border'})
        with self.assertRaises(ValueError):verified_passes(root,emitted,colors,{'body':8,'tiny':.9,'border':3})

    def test_exact_palette_keeps_rare_similar_color(self):
        rgb=np.full((800,600,3),[27,30,43],np.uint8)
        rgb[6,30:60]=[30,32,46]
        mask=np.ones(rgb.shape[:2],bool)
        with patch('studio_separation.cv2.kmeans',side_effect=AssertionError):
            labels,palette,_,method=separate_colors(rgb,mask,9,True)
        self.assertEqual(method,'exact')
        np.testing.assert_array_equal(palette[labels],rgb)

    def test_nested_hole_and_island_survive_resizing_without_retracing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            classes=np.full((90,120),255,np.uint8)
            for radius,color in [(36,0),(25,255),(17,0),(8,255)]:
                cv2.circle(classes,(60,45),radius,color,-1)
            np.save(root/'classes.npy',classes)
            art.save_json(root/'palette.json',{'width_px':120,'height_px':90,'detail_mode':'preserve','colors':[{}]})
            with patch.object(art,'detail_geometries',wraps=art.detail_geometries) as trace:
                _,groups,_,keys=art.extract_geometries(root,119,True)
                _,resized,_,other=art.extract_geometries(root,59.5,True)
                self.assertEqual(trace.call_count,1)
            self.assertEqual(keys,other)
            self.assertEqual([len(p.interiors) for p in groups[0]],[1,1])
            self.assertTrue(groups[0][0].disjoint(groups[0][1]))
            self.assertFalse(any(p.covers(Point(65,50)) for p in groups[0]))
            self.assertAlmostEqual(sum(p.area for p in resized[0]),sum(p.area for p in groups[0])/4)

    def test_centerline_distinguishes_open_lines_closed_lines_and_branches(self):
        curve=LineString([(0,0),(1,.1),(3,.45),(5,1),(6,1.7)]).buffer(.11)
        ring=Point(0,0).buffer(3).difference(Point(0,0).buffer(2.78))
        fork=box(0,0,6,.22).union(box(2.8,0,3,3))
        for p in [curve,ring]:
            self.assertEqual(recommend_stitch(p)['method'],'run')
            self.assertTrue(p.buffer(.015).covers(fine_line(p)[0]))
        self.assertTrue(fine_line(ring)[0].is_ring)
        self.assertIsNone(fine_line(fork))
        self.assertIsNone(fine_line(box(0,0,1,.1)))

    def test_only_defer_an_inlay_when_its_surround_would_sew_later(self):
        hole=Point(4,4).buffer(1)
        host=box(0,0,8,8).difference(hole)
        unrelated=box(9,9,10,10)
        self.assertEqual(inset_details([[hole,unrelated],[host]],[0,1]),{(0,0)})
        self.assertFalse(inset_details([[hole],[host]],[1,0]))
        result=machine_fill_geometry(host.segmentize(.02),.20,.20)
        self.assertEqual(len(result.interiors),1)
        self.assertTrue(result.boundary.hausdorff_distance(host.boundary)<=.051)

    def test_actual_files_keep_a_sewable_hairline_through_size_adaptation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);rgba=np.zeros((220,420,4),np.uint8)
            rgba[10:210,10:410]=[244,207,70,255]
            rgba[85:88,70:270]=[40,42,70,255]
            Image.fromarray(rgba).save(root/'source.png')
            art.flatten(root/'source.png',root/'art',9,detail_mode='preserve')
            fitted,_,_=fit_artwork(root/'art',root/'fit',30,'75D')
            self.assertEqual(len(fitted['colors']),2)
            v=make_plan(root/'fit',root/'machine',30,'75D',.20)
            self.assertTrue(v['stats']['sew_geometry_dst_pes_equal'])
            self.assertTrue(any(o['stitch_type']=='单针走针' for o in v['meta']['planned_objects']))
            self.assertFalse(v['stats']['physically_sewn'])

    def test_review_checks_transparent_holes_and_small_centerlines(self):
        ring=box(1,1,9,9).difference(box(3,3,7,7))
        hair=box(10,1,16,1.2)
        regions=[{'key':'ring','color':0,'geometry':ring},{'key':'hair','color':1,'geometry':hair}]
        moves=[]
        for y in np.arange(1,9,.1):moves.extend([[1,y,1,0],[9,y,0,0]])
        review=review_needle_geometry({'width':18,'height':11,'commands':moves,'meta':{'preview_thread_width_mm':.20}},regions)
        issues={issue['region_key']:issue for issue in review['issues']}
        self.assertGreater(issues['ring']['cutout_blocked_percent'],95)
        self.assertGreater(issues['hair']['interior_mismatch_percent'],95)

    def test_artwork_distinguishes_transparent_cutouts_from_gaps_between_traced_colours(self):
        ring=box(5,5,15,15).difference(box(8,8,12,12))
        regions=[{'key':'ring','color':0,'geometry':ring}]
        moves=[]
        for y in np.arange(5,15,.1):moves.extend([[5,y,1,0],[15,y,0,0]])
        view={'width':20,'height':20,'commands':moves,'meta':{'design_width_mm':10,'design_height_mm':10,
              'export_origin_mm':[10,10],'preview_thread_width_mm':.20}}
        source=np.zeros((101,101),np.uint8);source[30:71,30:71]=1
        self.assertFalse(review_needle_geometry(view,regions,source)['issues'])
        source[30:71,30:71]=255
        issue,=review_needle_geometry(view,regions,source)['issues']
        self.assertGreater(issue['cutout_blocked_percent'],95)

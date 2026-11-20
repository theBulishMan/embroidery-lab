import math
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from shapely.affinity import rotate, scale, translate
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

from studio_artwork import flatten
from studio_craft import region_recommendations
from studio_digitize import ENGINE, make_plan
from studio_local import catalog
from studio_quality import review_needle_geometry
from studio_structure import converging_surface, spreading_sections


class StructuralDirectionTests(unittest.TestCase):
    def test_spreading_field_is_rotation_invariant_and_tiles_the_actual_contour(self):
        arc=[(10*math.cos(t),10*math.sin(t)) for t in np.linspace(-.9,.9,61)]
        for angle in (0,38,173):
            shape=translate(rotate(Polygon([(0,0),*arc]),angle),15,10)
            field=converging_surface(shape)
            self.assertIsNotNone(field)
            hub,*panels=spreading_sections(shape,field,.2)
            pieces=[hub[0]]+[p for p,_ in panels]
            self.assertLess(unary_union(pieces).symmetric_difference(shape).area,.00001)
            self.assertAlmostEqual(sum(p.area for p in pieces),shape.area)
            pivot=Point(field[0])
            self.assertLess(hub[0].distance(pivot),1e-7)
            self.assertGreater(min(p.distance(pivot) for p,_ in panels),.59)
            self.assertGreater(len(set(a for _,a in panels)),3)
        for ordinary in (box(0,0,12,18),Point(0,0).buffer(8),Polygon([(0,0),(8,9),(-8,9)])):
            self.assertIsNone(converging_surface(ordinary))

    def test_separate_elongated_parts_use_their_own_axes_when_the_group_supports_it(self):
        oval=scale(Point(0,0).buffer(1),6,2.5)
        def parts(along):
            return [dict(key=str(i),color=0,geometry=translate(rotate(oval,theta if along else 20),
                         9*math.cos(math.radians(theta)),9*math.sin(math.radians(theta))))
                    for i,theta in enumerate((14,86,158,230,302))]
        goals=region_recommendations(parts(True),.20)
        self.assertEqual({g['structure'] for g in goals.values()},{'axis'})
        self.assertGreater(len({round(g['angle']) for g in goals.values()}),3)
        self.assertFalse(any(g['structure']=='axis' for g in region_recommendations(parts(False),.20).values()))

    def test_free_surface_honours_an_explicit_guide_and_checks_the_machine_direction(self):
        shape=box(5,5,15,15);region={'key':'a','color':0,'geometry':shape}
        meta={'preview_thread_width_mm':.2,'design_width_mm':10,'design_height_mm':10,
              'object_overrides':{'a':{'method':'guided','guide':[[0,.5],[.5,.5],[1,.5]]}},
              'planned_objects':[{'region_key':'a','role':'随曲线面针','stitch_type':'填针'}]}
        for along in (True,False):
            commands=[]
            for d in np.arange(5,15.01,.15):
                a,b=((5,d),(15,d)) if along else ((d,5),(d,15))
                commands.extend([(*a,1,0),(*b,0,0)])
            v={'width':20,'height':20,'meta':meta,'commands':commands}
            check=review_needle_geometry(v,[region])['stitch_suitability']
            self.assertEqual(check['regions']['a']['structure'],'guide')
            self.assertEqual(bool(check['issues']),not along)

    @unittest.skipUnless(ENGINE.is_file(),'requires the machine-file exporter')
    def test_dense_diagonal_control_does_not_pass_line_border_or_spreading_surface(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);im=Image.new('RGBA',(720,530));draw=ImageDraw.Draw(im)
            draw.pieslice((36,40,456,460),212,328,fill='#b85c37')
            draw.arc((58,232,343,427),31,149,fill='#3a6867',width=5)
            draw.ellipse((429,56,658,242),outline='#263f58',width=18)
            draw.rounded_rectangle((410,298,667,428),radius=21,fill='#e0a541')
            im.save(root/'sample.png')
            flatten(root/'sample.png',root/'art',9,preserve_white=True,detail_mode='preserve',width=48,thread='75D')
            regions=catalog(root/'art',48,thread='75D')['regions']
            by_role={r['recommendation']['structure']:r['id'] for r in regions}
            self.assertEqual(set(by_role),{'line','band','spread','surface'})
            diagonal={key:{'method':'fill','angle':45,'fill_spacing':.16} for role,key in by_role.items() if role!='surface'}
            for name,overrides in [('auto',{}),('diagonal',diagonal)]:
                result=make_plan(root/'art',root/name,48,'75D',.20,adaptive_fill=True,object_overrides=overrides)
                check=result['meta']['quality_review']['stitch_suitability']
                self.assertTrue(result['stats']['sew_geometry_dst_pes_equal'])
                self.assertEqual({i['region_key'] for i in check['issues']},set(overrides))
                self.assertEqual(check['direction_regions'],3)
                spread=check['regions'][by_role['spread']]
                self.assertGreater(spread['samples'],50)
                self.assertEqual(spread['aligned_percent']>80,name=='auto')

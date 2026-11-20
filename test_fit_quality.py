import json
import tempfile
import unittest
from pathlib import Path
import cv2
import numpy as np
from PIL import Image
from shapely.geometry import LineString, Point, box
from studio_artwork import save_json
from studio_craft import branched_ring_rails, protect_fill_recesses, rail_coverage, recommend_stitch, supported_fill_edge
from studio_fit import fit_artwork, widen_internal_gaps
from studio_quality import review_needle_geometry


class FitArtworkTests(unittest.TestCase):
    def test_closed_band_completes_a_turn_even_with_branches(self):
        opening=Point(0,0).buffer(2.3,quad_segs=32)
        ring=opening.buffer(1).difference(opening)
        shape=ring.union(LineString([(2.9,0),(5.4,.9)]).buffer(.48))
        shape=shape.union(LineString([(-2.9,0),(-5.1,1.3)]).buffer(.5))
        a,b,width=branched_ring_rails(shape)
        center=(np.asarray(a)+np.asarray(b))/2
        angle=np.unwrap(np.arctan2(center[:,1],center[:,0]))
        self.assertGreater(abs(angle[-1]-angle[0]),6.1)
        self.assertAlmostEqual(np.median(width),1,delta=.1)
        coverage=rail_coverage(a,b)
        self.assertLess(coverage.intersection(opening.buffer(-.05)).area,.02)
        self.assertLess(ring.difference(coverage.buffer(.07)).area,.08)
        self.assertLess(coverage.area,shape.area*.95)
        self.assertEqual(recommend_stitch(shape)['method'],'satin')
        self.assertIsNone(branched_ring_rails(box(-9,-6,9,6).difference(opening)))

    def source(self, folder):
        a=np.full((401,401),255,np.uint8)
        a[10:391,10:391]=0
        cv2.circle(a,(155,200),28,1,-1);cv2.circle(a,(245,200),28,1,-1)
        a[199:202,155:246]=1
        a[60:65,60:65]=2
        # A tiny hollow network must disappear as a whole, without its holes
        # being filled by the same deleted colour and resurrecting fragments.
        cv2.circle(a,(100,70),8,2,2)
        palette={'width_px':401,'height_px':401,'detail_mode':'preserve','max_colors':3,
                 'colors':[{'key':f'color-{i+1}','name':n,'rgb':c,'hex':h,'pixel_count':int((a==i).sum())} for i,(n,c,h) in enumerate([
                     ('白色',[250,250,250],'#FAFAFA'),('蓝色',[30,70,180],'#1E46B4'),('灰色',[120,120,120],'#787878')])]}
        folder.mkdir();np.save(folder/'classes.npy',a);save_json(folder/'palette.json',palette)
        return a

    def test_drops_tiny_details_without_punching_holes_or_changing_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=self.source(root/'source')
            m,v,r=fit_artwork(root/'source',root/'fit',40,'75D')
            out=np.load(root/'fit/classes.npy')
            self.assertTrue(np.array_equal(source,np.load(root/'source/classes.npy')))
            self.assertTrue(np.array_equal(source==255,out==255))
            self.assertFalse(any(c['hex']=='#787878' for c in m['colors']))
            self.assertGreaterEqual(r['separated_motifs'],1)
            self.assertGreater(np.count_nonzero(out==1),np.count_nonzero(source==1)*.6)
            self.assertEqual(out[200,200],0)
            self.assertFalse(r['physically_sewn'])

    def test_actual_size_changes_the_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.source(root/'source')
            _,_,small=fit_artwork(root/'source',root/'small',40,'75D')
            _,_,large=fit_artwork(root/'source',root/'large',160,'75D')
            self.assertGreater(small['removed_parts'],large['removed_parts'])

    def test_opposing_petals_keep_the_drawn_separator_at_small_size(self):
        art=np.full((100,140),255,np.uint8)
        art[8:92,6:134]=0
        art[24:76,20:68]=1
        art[24:76,72:120]=1
        # An intentional hole in a petal, plus an independent fine line.
        art[41:49,42:50]=255
        art[13:15,16:50]=2
        small,changes=widen_internal_gaps(art,.03,.20,3)
        large,noop=widen_internal_gaps(art,.15,.20,3)
        self.assertEqual(len(changes),2)
        self.assertTrue(np.all(small[52,65:75]==0))
        self.assertTrue(np.all(small[52,[47,97]]==1))
        np.testing.assert_array_equal(art==255, small==255)
        np.testing.assert_array_equal(art==2, small==2)
        np.testing.assert_array_equal(large, art)
        self.assertEqual(noop,[])

    def test_smooth_source_and_change_map_survive_size_fit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=self.source(root/'source')
            palette=root/'source'/'palette.json'
            meta=json.loads(palette.read_text());meta.pop('detail_mode')
            save_json(palette,meta)
            new,_,report=fit_artwork(root/'source',root/'fit',40,'75D')
            self.assertGreater(report['changed_area_mm2'],0)
            self.assertNotEqual(new.get('detail_mode'),'preserve')
            marked=np.asarray(Image.open(root/'fit'/'fit-changes.png'))
            before=np.asarray([x['rgb'] for x in meta['colors']]+[[0,0,0]])[np.where(source==255,len(meta['colors']),source)]
            flat=np.asarray(Image.open(root/'fit'/'flat.png'))
            delta=np.any(before!=flat[:,:,:3],axis=2)
            self.assertTrue(np.all(marked[delta]==[178,22,105,255]))
            self.assertFalse(np.any(np.all(flat==[178,22,105,255],axis=2)))

    def test_thread_clearance_on_a_concave_inlay_and_supported_edge(self):
        target=box(0,0,5,4).difference(box(2,2,3,5))
        clipped=protect_fill_recesses(target.buffer(.08),target,.20)
        self.assertTrue(clipped.disjoint(box(2.04,2.04,2.96,3.95)))
        broad=box(0,0,10,5)
        self.assertIs(supported_fill_edge(broad,broad,[],.20),broad)
        earlier=box(-3,-3,0,8).union(box(-3,-3,13,0))
        with_support=supported_fill_edge(broad,broad,[earlier],.20)
        self.assertGreater(with_support.bounds[0],.05)
        self.assertEqual(with_support.bounds[2:],broad.bounds[2:])


class NeedleReviewTests(unittest.TestCase):
    def test_detects_filled_negative_space_from_real_needle_moves(self):
        motif=box(1,1,9,9).difference(box(4,3,6,7))
        regions=[{'key':'white','color':0,'geometry':box(0,0,10,10).difference(motif)},
                 {'key':'flower','color':1,'geometry':motif}]
        commands=[]
        for y in np.arange(1,9.01,.1):commands.extend([[1,y,1,1],[9,y,0,1]])
        v={'width':10,'height':10,'commands':commands,'meta':{'preview_thread_width_mm':.2}}
        review=review_needle_geometry(v,regions)
        issue=next(x for x in review['issues'] if x['region_key']=='flower')
        self.assertGreater(issue['gap_blocked_percent'],90)
        self.assertEqual(review['status'],'needs_review')
        self.assertFalse(review['physically_sewn'])

    def test_jump_moves_are_not_counted_as_coverage(self):
        region={'key':'solid','color':0,'geometry':box(1,1,9,9)}
        v={'width':10,'height':10,'meta':{'preview_thread_width_mm':.2},
           'commands':[[1,y,1,0] for y in np.arange(1,9.01,.1)]+[[9,9,1,0]]}
        result=review_needle_geometry(v,[region])
        self.assertEqual(result['issues'][0]['interior_mismatch_percent'],100)


if __name__=='__main__':unittest.main()

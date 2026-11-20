import unittest
import cv2
import numpy as np
from shapely.geometry import LineString, Point, Polygon, box
from shapely.strtree import STRtree
from studio_craft import one_sided_satin_rails, protect_cloth_openings, round_group_directions, split_exposed_bands
from studio_craft import registered_tatami, restore_inlay_underlap
from studio_fit import join_pixel_tips
from studio_quality import inspect_feature_witnesses, review_needle_geometry
from studio_clearance import StrokeGaps
from studio_separation import antialiased_inks, compressed_inks, separate_colors


class OrdinaryArtworkTests(unittest.TestCase):
    def test_oblique_tatami_repeats_and_staggers_land_on_machine_grid(self):
        for requested in (75, 105):
            angle, pitch, length, staggers = registered_tatami(requested, .18, 3.2)
            self.assertLessEqual(abs(angle - requested), 1.5)
            self.assertLessEqual(pitch, .18)
            self.assertGreaterEqual(pitch, .168)
            self.assertLessEqual(length, 3.2)
            theta = np.radians(-angle)
            along = np.array([np.cos(theta), np.sin(theta)])
            across = np.array([-along[1], along[0]])
            for repeat in (length * along, (length / staggers) * along + pitch * across):
                np.testing.assert_allclose(repeat * 10, np.round(repeat * 10), atol=1e-5)
        self.assertIsNone(registered_tatami(90, .20, 3.2))
        self.assertIsNone(registered_tatami(8, .18, 3.2))

    def test_sewn_pale_inset_keeps_underlap_while_real_holes_and_gaps_stay_open(self):
        inset = Polygon([(2,2),(5,3),(5,4),(3,4)])
        opening = box(6,2,8,4)
        visible = box(0,0,10,6).difference(inset.union(opening))
        needle = visible.union(inset)
        cut_tip = Point(2.2,2.25).buffer(.24)
        cut_gap = Point(6,3).buffer(.24)
        clipped = needle.difference(cut_tip.union(cut_gap))
        # Separately traced inks have slightly different polygon areas.
        traced = inset.buffer(-.032)
        result = restore_inlay_underlap(needle, clipped, visible, [traced], .20)
        self.assertLess(result.symmetric_difference(needle.difference(cut_gap)).area, 1e-7)
        self.assertTrue(result.intersection(opening).is_empty or result.intersection(opening).area == 0)
        for other in ([], [box(2.8,2.8,3.3,3.2)], [inset.difference(Point(4,3.4).buffer(.10))]):
            self.assertIs(restore_inlay_underlap(needle, clipped, visible, other, .20), clipped)

    def test_round_spot_readback_penalizes_spilling_thread_even_with_full_coverage(self):
        disk=Point(2,2).buffer(.55,quad_segs=24)
        region={'key':'dot','color':0,'geometry':disk}
        meta={'preview_thread_width_mm':.20,'thread_spec':'75D','fabric':'twill'}
        def readback(radius):
            commands=[]
            for dy in np.arange(-radius+.01,radius,.11):
                half=(radius**2-dy**2)**.5
                commands.extend([(2-half,2+dy,1,0),(2+half,2+dy,0,0)])
            return review_needle_geometry({'width':4,'height':4,'meta':meta,'commands':commands},[region])
        self.assertFalse(readback(.48)['issues'])
        issue,=readback(.78)['issues']
        self.assertEqual(issue['interior_mismatch_percent'],0)
        self.assertTrue(any('小圆点外侧' in reason for reason in issue['reasons']))

    def test_free_curved_stem_keeps_its_round_end_and_connection(self):
        body=box(1,6,9,13)
        stem=LineString([(4,6.5),(4,4.7),(3.6,3.7),(3,3),(2,2.4)]).buffer(.29)
        tip=Point(2,2.4)
        shape=body.union(stem).union(tip.buffer(.8))
        rest,bands=split_exposed_bands(shape,shape,.20,lambda part:True)
        self.assertEqual(len(bands),1)
        band=bands[0]
        self.assertTrue(band.contains(tip))
        self.assertGreater(rest.intersection(band).area,.01)
        self.assertLess(shape.symmetric_difference(rest.union(band)).area,.02)
        self.assertIsNone(split_exposed_bands(shape,shape,.20,lambda part:False))
        bridge=body.union(stem).union(tip.buffer(2.5))
        self.assertIsNone(split_exposed_bands(bridge,bridge,.20,lambda part:True))

    def test_lost_tip_or_thread_spilling_across_it_still_fails_readback(self):
        from shapely import contains_xy
        y,x=np.mgrid[:160,:260]*.03
        mask=lambda shape:contains_xy(shape,x,y)
        shape=box(1,1,4,4).union(box(4,2.25,6.7,2.64))
        intended=mask(shape).astype(np.uint8)
        def verify(surface):
            return inspect_feature_witnesses(shape,1,intended,surface,mask,.03,.20,'75D')
        self.assertTrue(verify(intended)['checked_narrow'])
        missing=intended.copy();missing[x>6.1]=0
        self.assertIsNone(verify(missing))
        widened=intended.copy();widened[mask(box(5,2,6.65,2.8))]=1
        self.assertIsNone(verify(widened))

    def test_photo_brief_keeps_size_and_does_not_replace_a_flat_art_edit(self):
        import io
        from PIL import Image
        from studio_artwork import has_continuous_tones
        from studio_service import style_prompt
        def image_stream(pixels):
            stream=io.BytesIO()
            Image.fromarray(pixels).save(stream,format='PNG')
            stream.seek(0)
            return stream
        rng=np.random.default_rng(81)
        self.assertTrue(has_continuous_tones(image_stream(rng.integers(0,255,(124,160,3),dtype=np.uint8))))
        a=np.full((124,160,3),255,np.uint8);a[12:90,40:110]=[23,98,50]
        self.assertFalse(has_continuous_tones(image_stream(a)))
        settings={'width':65,'thread':'75D'}
        brief=style_prompt('保留花纹',9,'upload','#0000FF',settings=settings,photographic=True)
        for item in ('65 mm','75D','主纹色','起止点','保留花纹','#0000FF'):
            self.assertIn(item,brief)
        self.assertNotIn('从参考照片描',style_prompt('红色',9,'edit',settings=settings,photographic=True))

    def test_small_palette_fringe_merges_but_an_opaque_hairline_and_white_highlight_survive(self):
        image=np.full((80,80,3), (24,32,36), np.uint8)
        image[:,42:]=(186,146,84)
        image[:,41]=(105,89,60)
        image[11:57,18]=(19,206,151)
        image[61:68,8:16]=255
        labels, inks, _, system=separate_colors(image,np.ones((80,80),bool),9,True)
        self.assertEqual((len(inks),system),(4,'solid-inks'))
        np.testing.assert_array_equal(inks[labels[11:57,18]],image[11:57,18])
        self.assertEqual(inks[labels[64,12]].tolist(),[255,255,255])
        self.assertNotIn([105,89,60],inks.tolist())
        image[4:33,11]=(26,34,39)
        labels,inks,_,_=separate_colors(image,np.ones((80,80),bool),9,True)
        np.testing.assert_array_equal(inks[labels[4:33,11]],image[4:33,11])
        self.assertNotIn([105,89,60],inks.tolist())

    def test_occluded_underpaint_can_tint_an_opaque_antialias_edge(self):
        image=np.zeros((84,90,3),np.uint8)
        image[8:75,9:32]=(92,178,230)
        image[8:75,42:70]=(185,153,198)
        image[8:75,37]=(20,38,49)
        mask=np.zeros(image.shape[:2],bool)
        mask[8:75,9:38]=True
        mask[8:75,42:70]=True
        alpha=mask.astype(np.uint8)*255
        labels,inks,_=antialiased_inks(image,mask,9,alpha)
        self.assertEqual(len(inks),3)
        self.assertEqual(inks[labels[42,37]].tolist(),[0,0,0])
        self.assertEqual(labels[42,38],255)

    def test_cloth_and_white_channels_keep_the_same_open_path_and_only_block_the_intruding_ink(self):
        colors=[{'hex':'#191919'},{'hex':'#ffffff'},{'hex':'#e34235'}]
        for gap_color in (255,1):
            with self.subTest(gap_color=gap_color):
                a=np.full((80,100),255,np.uint8);a[10:21,10:80]=0;a[24:35,10:80]=0
                a[21:24,10:80]=gap_color
                g=StrokeGaps(a,colors,.08,.24)
                across=LineString([(8.5,6.3),(8.5,7.3)])
                self.assertTrue(g.blocks_link(0,across))
                self.assertFalse(g.blocks_link(1,across))
                self.assertFalse(g.blocks_link(0,LineString([(6.1,6.1),(10.8,6.1)])))
                shape=box(6.0,5.8,10.8,6.72)
                clipped=g.clip(shape,0,.05)
                self.assertLess(clipped.area,shape.area)
                self.assertTrue(clipped.covers(Point(8.5,6.05)))
                rails=g.clip_rails([(7,5.86),(8,5.86)],[(7,6.78),(8,6.78)],0,.04)
                np.testing.assert_allclose(rails[0],[(7,5.86),(8,5.86)])
                self.assertLess(rails[1,:,1].max(),6.78)
                # The same two regions in direct colour contact have no
                # intended blank strip to trim.
                a[21:24,10:80]=2
                self.assertFalse(StrokeGaps(a,colors,.08,.24).blocks_link(0,across))

    def test_same_connected_strokes_and_rotated_gaps_do_not_require_separate_objects(self):
        a=np.full((96,96),255,np.uint8)
        for y in (31,39):
            cv2.line(a,(20,y),(63,y+43),0,3)
        cv2.line(a,(20,31),(20,39),0,3)
        guards=StrokeGaps(a,[{'hex':'#232323'}],.07,.20)
        self.assertTrue(guards.blocks_link(0,LineString([(7.8,8.57),(7.8,9.13)])))
        self.assertFalse(guards.blocks_link(0,LineString([(8.8,6.3),(9.5,6.3)])))

    def test_solid_inks_do_not_invent_boundary_ink_or_hide_a_new_stroke(self):
        rgb=np.full((48,60,3),[240,202,79],np.uint8)
        rgb[:,30:]=[45,92,143]
        rgb[:,30]=[142,147,111]
        mask=np.ones(rgb.shape[:2],bool)
        labels,inks,_=antialiased_inks(rgb,mask,9)
        self.assertEqual(len(inks),2)
        self.assertEqual(len(np.unique(labels)),2)
        rgb[4:30,6]=[229,20,189]
        labels,inks,_=antialiased_inks(rgb,mask,9)
        self.assertEqual(len(inks),3)
        np.testing.assert_array_equal(inks[labels[4:30,6]],rgb[4:30,6])
        # Even small, distinct two-dimensional spots have a valid core.
        rgb[6:16,4:12]=[229,20,189]
        self.assertEqual(len(antialiased_inks(rgb,mask,9)[1]),3)

    def test_jpeg_illustration_reuses_inks_and_preserves_an_enclosed_white_mark(self):
        import io
        from PIL import Image, ImageDraw
        img=Image.new('RGB',(160,140),'white');draw=ImageDraw.Draw(img)
        draw.rounded_rectangle((12,12,150,126),18,fill=(222,46,67))
        draw.ellipse((40,34,100,110),fill=(40,78,112));draw.ellipse((56,51,78,68),fill='white')
        buf=io.BytesIO();img.save(buf,'JPEG',quality=86);buf.seek(0)
        rgb=np.array(Image.open(buf));near_white=(rgb.min(2)>237)&((rgb.max(2)-rgb.min(2))<16)
        _,parts=cv2.connectedComponents(near_white.view(np.uint8))
        outside=parts[0,0];mask=parts!=outside
        labels,inks,_=compressed_inks(rgb,mask,9,True)
        self.assertEqual(len(inks),3)
        self.assertTrue(np.all(inks[labels[60,65]]>=240))
        self.assertEqual(labels[2,2],255)
        kept,_,_=compressed_inks(rgb,np.ones(mask.shape,bool),9,False)
        self.assertNotEqual(kept[2,2],255)
        # A one-pixel contrasting stroke on one field is evidence against
        # folding this source into just the dominant palette.
        rgb[17:47,19]=[32,200,124]
        self.assertIsNone(compressed_inks(rgb,mask,9,True))

    def test_photographic_texture_falls_back_to_full_separation(self):
        rng=np.random.default_rng(41)
        rgb=rng.integers(0,255,(96,128,3),dtype=np.uint8)
        self.assertIsNone(compressed_inks(rgb,np.ones((96,128),bool),9,False))

    def test_diagonal_pixel_can_join_ink_but_never_closes_a_cloth_cutout(self):
        a=np.zeros((14,14),np.uint8)
        a[4:8,4:8]=1;a[8,8]=1
        after,changes=join_pixel_tips(a,.06,.20,2)
        self.assertEqual(len(changes),1)
        self.assertEqual(np.count_nonzero(after!=a),1)
        a[a==0]=255
        np.testing.assert_array_equal(join_pixel_tips(a,.06,.20,2)[0],a)

    def test_satin_compensation_only_moves_the_rail_backed_by_an_earlier_colour(self):
        a=np.column_stack((np.linspace(1,9,20),np.full(20,2.)))
        b=a+[0.,2.];widths=np.full(20,2.0)
        backing=box(0,0,10,2);ribbon=box(1,2,9,4)
        tree=STRtree([backing,ribbon]);colors=np.array([0,1])
        adjusted=one_sided_satin_rails(a,b,widths,.20,.12,tree,colors,1)
        self.assertTrue(np.all(adjusted[0][:,1]>a[:,1]))
        np.testing.assert_array_equal(adjusted[1],b)
        self.assertIsNone(one_sided_satin_rails(a,b,widths,.20,.12,tree,np.array([1,1]),1))
        self.assertIsNone(one_sided_satin_rails(a,b,widths,.20,.12,STRtree([ribbon,backing]),colors,0))

    def test_round_fills_follow_their_group_without_changing_elongated_or_isolated_marks(self):
        regions = [{'geometry': Point(x, y).buffer(3), 'color': 0, 'key': name}
                   for x, y, name in [(8,0,'right'), (-8,0,'left'), (0,8,'bottom'), (0,-8,'top')]]
        regions += [{'geometry': box(10,10,22,11), 'color': 0, 'key': 'bar'},
                    {'geometry': Point(0,20).buffer(3), 'color': 1, 'key': 'single'}]
        expected = {'right': 0, 'left': 0, 'top': 90, 'bottom': 90}
        self.assertEqual(round_group_directions(regions), expected)
        self.assertEqual(round_group_directions(list(reversed(regions))), expected)

    def test_tatami_keeps_the_footprint_out_of_cloth_but_leaves_a_sewn_inlay_alone(self):
        opening = Point(6,6).buffer(1)
        inlay = Point(12,6).buffer(1)
        shape = box(0,0,18,12).difference(opening.union(inlay))
        objects = [({'geometry': shape}, 'body'), ({'geometry': inlay}, 'highlight')]
        tree = STRtree([shape, inlay])
        protected = protect_cloth_openings(shape, shape, objects, 0, tree, .24, .05)
        self.assertTrue(protected.exterior.equals(shape.exterior))
        holes = [Polygon(ring) for ring in protected.interiors]
        self.assertTrue(any(h.equals(inlay) for h in holes))
        self.assertTrue(any(h.contains(opening.buffer(.16)) for h in holes))
        thin = opening.buffer(.09).difference(opening)
        self.assertIs(protect_cloth_openings(thin, thin, [({'geometry': thin},'body')],
                                             0, STRtree([thin]), .24, .05), thin)

    def test_rechecking_an_inlay_requires_actual_top_thread_and_real_clearance(self):
        precision=.03
        def mask(geom):
            m=np.zeros((340,340),np.uint8)
            if geom.is_empty:return m.astype(bool)
            for g in getattr(geom,'geoms',(geom,)):
                if g.geom_type!='Polygon':continue
                pts=lambda ring:np.rint(np.asarray(ring.coords)/precision).astype(np.int32)
                cv2.fillPoly(m,[pts(g.exterior)],1)
                for hole in g.interiors:cv2.fillPoly(m,[pts(hole)],0)
            return m.astype(bool)
        middle=box(2,4,7,4.30)
        p=box(1,1,9,8).difference(middle)
        target=mask(p).astype(np.uint8);target[mask(middle)]=2
        def inspect(p,expected,top):
            return inspect_feature_witnesses(p,1,expected,top,mask,precision,.2,'75D')
        self.assertTrue(inspect(p,target,target)['checked_inlays'])
        self.assertIsNone(inspect(p,target,(target>0).astype(np.uint8)))
        target[mask(middle)]=0
        self.assertIsNone(inspect(p,target,target))
        # Two coloured stripes less than two strands apart must still be reviewed.
        second=box(2,4.44,7,4.75)
        both=p.difference(second);target[mask(middle)|mask(second)]=2
        self.assertIsNone(inspect(both,target,target))

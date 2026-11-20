import unittest
from shapely.geometry import box
from shapely.affinity import scale
from studio_feasibility import detail_metrics,fill_edge_allowance


class DetailFitTests(unittest.TestCase):
    def test_large_shape_does_not_hide_small_hole(self):
        shape=box(0,0,12,12).difference(box(5,5,5.2,7))
        before=shape.wkb
        facts=detail_metrics(shape)
        self.assertGreater(facts['width_mm'],5)
        self.assertAlmostEqual(facts['smallest_hole_mm'],.2,places=3)
        self.assertEqual(shape.wkb,before)
        self.assertAlmostEqual(detail_metrics(scale(shape,2,2))['smallest_hole_mm'],.4,places=3)

    def test_large_object_with_thin_branch_is_reviewed(self):
        shape=box(0,0,4,4).union(box(4,1.8,12,2.2))
        facts=detail_metrics(shape)
        self.assertGreater(facts['width_mm'],1)
        self.assertGreater(facts['narrow_fraction']['75D'],.04)
        normal=detail_metrics(box(0,0,12,12))
        self.assertLess(normal['narrow_fraction']['75D'],.01)
        self.assertIsNone(normal['smallest_hole_mm'])

    def test_narrow_line_retains_its_measured_width(self):
        facts=detail_metrics(box(0,0,8,.35))
        self.assertAlmostEqual(facts['width_mm'],.35,places=3)

    def test_margins_protect_motifs_but_preserve_manual_compensation(self):
        shape=box(0,0,4,4).union(box(4,1.8,12,2.2))
        before=shape.wkb
        auto=fill_edge_allowance(shape,'75D',.12)
        self.assertEqual(auto['expansion_mm'],0)
        self.assertEqual(auto['pull_compensation_mm'],.02)
        self.assertTrue(auto['detail_edge_protection'])
        self.assertEqual(fill_edge_allowance(shape,'75D',.12,.17)['pull_compensation_mm'],.17)
        self.assertEqual(fill_edge_allowance(box(0,0,12,12),'75D',.12)['pull_compensation_mm'],.12)
        self.assertEqual(shape.wkb,before)


if __name__=='__main__':unittest.main()

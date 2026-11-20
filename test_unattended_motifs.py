import unittest
from studio_fit import omitted_motifs

class SourceMotifTests(unittest.TestCase):
    def test_grouped_original_marks_survive_dust_filter(self):
        def change(x, area, color=1, reason='省略细纹'):
            return dict(color=color,area_mm2=area,reason=reason,bbox_mm=[x,5,x+.17,6.65])
        records=[change(i*.56,.27) for i in range(15)]+[change(i*.53,.012,2) for i in range(20)]+[change(2,.28,3,'保留细线')]
        issues=omitted_motifs(records,.24)
        self.assertEqual([(a['color'],a['count']) for a in issues],[(1,15)])
        self.assertGreater(issues[0]['area_mm2'],4)
    def test_significant_isolated_mark_is_detected(self):
        self.assertEqual(omitted_motifs([dict(color=1,reason='省略',area_mm2=2,bbox_mm=[3,3,4,5])],.2)[0]['count'],1)

    def test_stop_uses_height_over_width_and_small_losses_still_reach_engine(self):
        from studio_fit import unattended_fit_stop
        report={'width':40,'thread':'75D','omitted_motifs':[{'count':5,'area_mm2':1.2} for _ in range(6)]}
        self.assertIsNotNone(unattended_fit_stop(report, .5))  # 40 x 20
        self.assertIsNone(unattended_fit_stop(report, 2))  # 40 x 80
        self.assertIsNone(unattended_fit_stop({**report,'omitted_motifs':report['omitted_motifs'][:3]}, .5))

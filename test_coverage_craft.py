import tempfile
import unittest
from pathlib import Path
from lxml import etree as ET
from shapely.geometry import box
import pyembroidery as pe
from studio_artwork import SVG,svg_root
from studio_craft import recommend_stitch,satin_candidate
from studio_digitize import set_export_origin,export_engine,param

class CoverageCraftTests(unittest.TestCase):
    def test_automatic_choice_uses_real_rail_feasibility(self):
        for polygon,expected in [(box(0,0,15,12),'fill'),(box(0,0,1.5,10),'satin'),(box(0,0,.4,10),'running')]:
            with self.subTest(expected=expected):
                recommendation=recommend_stitch(polygon)
                self.assertEqual(recommendation['method'],expected)
                self.assertEqual(satin_candidate(polygon)[0] is not None,expected!='fill')

    def test_export_readback_preserves_artwork_coordinates_not_bounds(self):
        with tempfile.TemporaryDirectory() as temp:
            root=svg_root(30,30);origin=(15,15);set_export_origin(root,*origin)
            path=ET.SubElement(root,f'{{{SVG}}}path',d='M 7,8 L 21,8 L 21,18',fill='none',stroke='#112233')
            for k,v in {'stroke_method':'running_stitch','running_stitch_length_mm':2}.items():param(path,k,v)
            dst,pes=export_engine(root,Path(temp))
            points=[(x/10+origin[0],y/10+origin[1]) for x,y,c in dst.stitches if c&255==pe.STITCH]
            self.assertAlmostEqual(min(x for x,y in points),7,delta=.11)
            self.assertAlmostEqual(max(x for x,y in points),21,delta=.11)
            self.assertAlmostEqual(min(y for x,y in points),8,delta=.11)
            self.assertAlmostEqual(max(y for x,y in points),18,delta=.11)

if __name__=='__main__':unittest.main()

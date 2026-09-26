import importlib.util
from pathlib import Path
import unittest
spec=importlib.util.spec_from_file_location("geometry",Path(__file__).resolve().parents[1]/"easy_pcb_order"/"geometry.py")
geometry=importlib.util.module_from_spec(spec)
spec.loader.exec_module(geometry)

class OutlineTests(unittest.TestCase):
    def test_front_panel_holes_and_arc(self):
        rectangle=[(0,0),(100,0),(100,60),(0,60),(0,0)]
        hole=geometry.circle_points((20,30),4,0.05)
        curved=[(120,0),(160,0)]+geometry.arc_points((160,0),(170,20),(160,40),0.05)[1:]+[(120,40),(120,0)]
        paths=[(rectangle,'panel'),(hole+[hole[0]],'hole'),(curved,'second')]
        boards=geometry.classify(geometry.stitch(paths,0.01))
        self.assertEqual(len(boards),2)
        self.assertEqual(len(boards[0]['holes']),1)
        self.assertEqual(set(boards[0]['edges']),{'panel','hole'})
        self.assertEqual(geometry.owner((50,30),boards),0)
        self.assertEqual(geometry.owner((140,20),boards),1)
        with self.assertRaises(ValueError): geometry.owner((20,30),boards)
    def test_arc_reversed_and_segments(self):
        arc=geometry.arc_points((0,10),(10,20),(20,10),0.05)
        loops=geometry.stitch([([(0,0),(20,0)],'bottom'), ([(20,0),(20,10)],'right'),
             (list(reversed(arc)),'arc'), ([(0,10),(0,0)],'left')],0.01)
        self.assertEqual(len(loops),1)
        self.assertGreater(loops[0]['area'],200)
    def test_open_contour_rejected(self):
        with self.assertRaises(ValueError): geometry.stitch([([(0,0),(10,0)],'line')],0.01)

if __name__=='__main__': unittest.main()

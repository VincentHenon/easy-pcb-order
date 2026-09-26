import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

class Footprint:
    def __init__(self,ref,value,footprint,smd=True):
        self.ref,self.value,self.footprint,self.smd=ref,value,footprint,smd
    def GetReference(self):return self.ref
    def GetValue(self):return self.value
    def GetFPID(self):return types.SimpleNamespace(GetLibItemName=lambda:self.footprint)
    def GetAttributes(self):return 1 if self.smd else 0

class GroupTests(unittest.TestCase):
    def test_groups_even_when_existing_assignments_differ(self):
        wx=types.ModuleType('wx');wx.Dialog=type('Dialog',(),{})
        pcb=types.ModuleType('pcbnew');pcb.FP_SMD=1
        preview=types.ModuleType('easy_pcb_order.preview')
        package=types.ModuleType('easy_pcb_order')
        package.__path__=[str(Path(__file__).resolve().parents[1]/'easy_pcb_order')]
        spec=importlib.util.spec_from_file_location('easy_pcb_order.parts',Path(__file__).resolve().parents[1]/'easy_pcb_order'/'parts.py')
        module=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,{'wx':wx,'pcbnew':pcb,'easy_pcb_order':package,'easy_pcb_order.preview':preview}):
            spec.loader.exec_module(module)
            fps=[Footprint('R1','10 kΩ','R_0603'),Footprint('R2','10kΩ','R_0603'),
                 Footprint('R3','1k','R_0603'),Footprint('C1','10kΩ','R_0603')]
            groups=module.group_footprints(fps,{'R1':'C100','R2':'C200'})
            merged,completed=module.apply_imported_groups(fps,{'R2':'C200'},{'R1':'C100'})
            expected_key=module.part_key(fps[0])
        self.assertEqual([[fp.ref for fp in group] for group in groups],[['R1','R2'],['R3'],['C1']])
        self.assertEqual(merged['R2'],'C100')
        self.assertIn(expected_key,completed)

if __name__=='__main__':unittest.main()

import csv
import importlib.util
from pathlib import Path
import re
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]/'easy_pcb_order'

def load_plugin():
    package=types.ModuleType('easy_pcb_order')
    package.__path__=[str(ROOT)]
    pcb=types.ModuleType('pcbnew')
    pcb.Edge_Cuts=44
    pcb.FromMM=lambda x:x
    pcb.ToMM=lambda x:x
    pcb.ActionPlugin=type('ActionPlugin',(),{})
    pcb.FP_SMD=1
    pcb.VIATYPE_THROUGH=1
    pcb.PCB_TRACK=type('PCB_TRACK',(),{})
    pcb.PCB_VIA=type('PCB_VIA',(pcb.PCB_TRACK,),{})
    pcb.ZONE=type('ZONE',(),{})
    wx=types.ModuleType('wx')
    wx.Dialog=type('Dialog',(),{})
    parts=types.ModuleType('easy_pcb_order.parts')
    parts.PART=re.compile(r'^C[1-9][0-9]*$',re.I)
    parts.PartsDialog=object
    parts.ReviewDialog=object
    parts.part_key=lambda fp:(fp.GetReference()[0],fp.GetValue(),fp.GetFPID().GetLibItemName(),fp.GetAttributes())
    parts.read_assignments=lambda x:{}
    parts.write_assignments=lambda x,y:None
    parts.read_stock_notes=lambda x:{}
    parts.write_stock_notes=lambda x,y:None
    wizard=types.ModuleType('easy_pcb_order.wizard')
    spec=importlib.util.spec_from_file_location('easy_pcb_order.plugin',ROOT/'plugin.py')
    module=importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules,{'easy_pcb_order':package,'pcbnew':pcb,'wx':wx,
                                 'easy_pcb_order.parts':parts,'easy_pcb_order.wizard':wizard}):
        spec.loader.exec_module(module)
    return module

class FakeFootprint:
    def __init__(self,ref,smd):self.ref=ref;self.smd=smd
    def GetReference(self):return self.ref
    def GetValue(self):return '10k'
    def GetFPID(self):return types.SimpleNamespace(GetLibItemName=lambda:'R_0603')
    def GetAttributes(self):return 1 if self.smd else 0
    def GetPosition(self):return types.SimpleNamespace(x=10,y=20)
    def GetOrientationDegrees(self):return 0
    def IsFlipped(self):return False
    def GetFields(self):return []

class ProfileTests(unittest.TestCase):
    def test_two_layer_design_keeps_through_via(self):
        plugin=load_plugin()
        via=plugin.pcbnew.PCB_VIA()
        via.GetLayer=lambda:0
        via.GetViaType=lambda:plugin.pcbnew.VIATYPE_THROUGH
        via.HasValidLayerPair=lambda count:True
        board=types.SimpleNamespace(GetCopperLayerCount=lambda:4,GetTracks=lambda:[via],
                 Zones=lambda:[],GetDrawings=lambda:[],GetLayerID=lambda name:name)
        with patch.object(plugin,'item_owner',return_value=0):
            plugin.validate_design_layers(board,0,[{}],2)
            via.GetViaType=lambda:2
            via.HasValidLayerPair=lambda count:False
            with self.assertRaisesRegex(ValueError,'via borgne'):
                plugin.validate_design_layers(board,0,[{}],2)
    def test_factory_bom_and_smd_only_position(self):
        plugin=load_plugin()
        board=types.SimpleNamespace(GetFootprints=lambda:[FakeFootprint('R1',True),FakeFootprint('R2',False)])
        for factory,expected in [('JLCPCB','LCSC Part #'),('PCBWay','Distributors Part Number'),
                                 ('Autre fabricant (Gerber + BOM + placement)','Supplier Part Number')]:
            with self.subTest(factory=factory),tempfile.TemporaryDirectory() as directory:
                count=plugin.export_assembly(board,Path(directory),factory,{'R1':'C100','R2':'C200'})
                self.assertEqual(count,(2,1))
                with open(Path(directory)/'bom.csv',encoding='utf-8-sig',newline='') as file:
                    rows=list(csv.reader(file))
                self.assertIn(expected,rows[0])
                self.assertEqual(len(rows),3)
                with open(Path(directory)/'positions.csv',encoding='utf-8-sig',newline='') as file:
                    rows=list(csv.reader(file))
                self.assertEqual(len(rows),2)
                self.assertEqual(rows[1][0],'R1')

if __name__=='__main__':unittest.main()

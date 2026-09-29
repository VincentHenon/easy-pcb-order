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
    pcb.F_SilkS=37
    pcb.B_SilkS=36
    pcb.VIATYPE_THROUGH=1
    pcb.PCB_TRACK=type('PCB_TRACK',(),{})
    pcb.PCB_VIA=type('PCB_VIA',(pcb.PCB_TRACK,),{})
    pcb.ZONE=type('ZONE',(),{})
    wx=types.ModuleType('wx')
    wx.Dialog=type('Dialog',(),{})
    parts=types.ModuleType('easy_pcb_order.parts')
    parts.PART=re.compile(r'^C[1-9][0-9]*$',re.I)
    parts.PartsDialog=object
    parts.ImportDialog=object
    parts.ReviewDialog=object
    parts.part_key=lambda fp:(fp.GetReference()[0],fp.GetValue(),fp.GetFPID().GetLibItemName(),fp.GetAttributes())
    parts.group_footprints=lambda fps,mapping:[[fp] for fp in fps]
    parts.apply_imported_groups=lambda fps,mapping,imported:(mapping,set())
    parts.read_assignments=lambda x:{}
    parts.write_assignments=lambda x,y:None
    parts.read_stock_notes=lambda x:{}
    parts.write_stock_notes=lambda x,y:None
    wizard=types.ModuleType('easy_pcb_order.wizard')
    preview=types.ModuleType('easy_pcb_order.preview')
    preview.find_cli=lambda:'/mock/kicad-cli'
    spec=importlib.util.spec_from_file_location('easy_pcb_order.plugin',ROOT/'plugin.py')
    module=importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules,{'easy_pcb_order':package,'pcbnew':pcb,'wx':wx,
                                 'easy_pcb_order.parts':parts,'easy_pcb_order.wizard':wizard,
                                 'easy_pcb_order.preview':preview}):
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
    def test_can_hide_only_silkscreen_reference_text(self):
        plugin=load_plugin()
        visible=types.SimpleNamespace(GetLayer=lambda:plugin.pcbnew.F_SilkS,SetVisible=lambda value:setattr(visible,'shown',value))
        fab=types.SimpleNamespace(GetLayer=lambda:99,SetVisible=lambda value:setattr(fab,'shown',value))
        visible.shown=True;fab.shown=True
        board=types.SimpleNamespace(GetFootprints=lambda:[types.SimpleNamespace(Reference=lambda:visible),types.SimpleNamespace(Reference=lambda:fab)])
        plugin.set_silkscreen_references(board,False)
        self.assertFalse(visible.shown)
        self.assertTrue(fab.shown)
        plugin.set_silkscreen_references(board,True)
        self.assertTrue(fab.shown)
    def test_mutating_helpers_copy_the_open_board_before_loading(self):
        plugin=load_plugin()
        plugin_source=(ROOT/'plugin.py').read_text(encoding='utf-8')
        preview_source=(ROOT/'preview.py').read_text(encoding='utf-8')
        output=plugin_source[plugin_source.index('def output_one'):plugin_source.index('def validate_design_layers')]
        prepare=preview_source[preview_source.index('def prepare_single'):preview_source.index('def render_prepared')]
        self.assertLess(output.index('shutil.copy2(source,temp)'),output.index('pcbnew.LoadBoard(str(temp))'))
        self.assertLess(prepare.index('shutil.copy2(source,pcb)'),prepare.index('pcbnew.LoadBoard(str(pcb))'))
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
                bom_name='BOM.csv' if factory=='JLCPCB' else 'bom.csv'
                cpl_name='CPL.csv' if factory=='JLCPCB' else 'positions.csv'
                with open(Path(directory)/bom_name,encoding='utf-8-sig',newline='') as file:
                    rows=list(csv.reader(file))
                self.assertIn(expected,rows[0])
                self.assertEqual(len(rows),3)
                with open(Path(directory)/cpl_name,encoding='utf-8-sig',newline='') as file:
                    rows=list(csv.reader(file))
                self.assertEqual(len(rows),2)
                self.assertEqual(rows[1][0],'R1')
                if factory=='JLCPCB':self.assertEqual(rows[0],['Designator','Mid X','Mid Y','Layer','Rotation'])
    def test_jlc_bom_groups_designators_for_one_part(self):
        plugin=load_plugin()
        board=types.SimpleNamespace(GetFootprints=lambda:[FakeFootprint('R1',True),FakeFootprint('R2',True)])
        with tempfile.TemporaryDirectory() as directory:
            plugin.export_assembly(board,Path(directory),'JLCPCB',{'R1':'C25804','R2':'C25804'})
            with open(Path(directory)/'BOM.csv',encoding='utf-8-sig',newline='') as file:rows=list(csv.reader(file))
        self.assertEqual(rows,[['Comment','Designator','Footprint','LCSC Part #'],['10k','R1,R2','R_0603','C25804']])

if __name__=='__main__':unittest.main()

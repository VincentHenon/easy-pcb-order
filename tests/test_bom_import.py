import tempfile
import importlib.util
from pathlib import Path
import types
import unittest

spec=importlib.util.spec_from_file_location('bom_import_test',Path(__file__).resolve().parents[1]/'easy_pcb_order'/'bom_import.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
parse_bom=module.parse_bom
parse_cpl=module.parse_cpl

class Footprint:
    def __init__(self,ref,value,footprint):self.ref,self.value,self.footprint=ref,value,footprint
    def GetReference(self):return self.ref
    def GetValue(self):return self.value
    def GetFPID(self):return types.SimpleNamespace(GetLibItemName=lambda:self.footprint)

class XmlBomTests(unittest.TestCase):
    def parse(self,xml):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'bom.xml';path.write_text(xml,encoding='utf-8')
            return parse_bom(path,[Footprint('R1','10k','R_0603'),Footprint('R2','10k','R_0603')])
    def test_partial_bom_and_property_field(self):
        result=self.parse('<export><components><comp ref="R1"><value>10k</value><footprint>Resistor_SMD:R_0603</footprint><fields><field name="LCSC Part #">C25804</field></fields></comp><comp ref="R2"><property name="JLCPCB" value="C123"/></comp></components></export>')
        self.assertEqual(result,({'R1':'C25804','R2':'C123'},0,0))
    def test_xlsx_like_jlcpcb_headers_and_multiple_refs(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'bom.csv';path.write_text('Comment,Designator,Footprint,JLCPCB Part #（optional）\n10k,"R1,R2",R0603,C25804\n',encoding='utf-8')
            self.assertEqual(parse_bom(path,[Footprint('R1','10k','R_0603'),Footprint('R2','10k','R_0603')]),({'R1':'C25804','R2':'C25804'},0,0))
    def test_cpl_columns(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'cpl.csv';path.write_text('Designator,Mid X,Mid Y,Rotation,Layer\nR1,84.9845,-55.8292,270,top\n',encoding='utf-8')
            self.assertEqual(parse_cpl(path,[Footprint('R1','10k','R_0603')]),({'R1':(84.9845,-55.8292,270.0,'Top')},0,0))
    def test_rejects_doctype(self):
        with self.assertRaisesRegex(ValueError,'déclarations externes'):
            self.parse('<!DOCTYPE export [<!ENTITY x "test">]><export><components/></export>')

if __name__=='__main__':unittest.main()

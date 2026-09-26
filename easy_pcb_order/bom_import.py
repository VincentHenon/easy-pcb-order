"""Import KiCad's XML BOM/netlist by explicit reference and LCSC field."""
from pathlib import Path
import re
import xml.etree.ElementTree as ET

PART=re.compile(r'^C[1-9][0-9]*$',re.I)
FIELD_NAMES={'lcsc','lcscpart','lcscpartnumber','jlcpcb','jlcpcbpart','jlcpcbpartnumber'}

def normalize_name(name):return re.sub(r'[^a-z0-9]','',(name or '').lower())

def parse_kicad_xml(path,footprints):
    source=Path(path)
    if source.stat().st_size>8*1024*1024:raise ValueError('BOM XML trop volumineuse (limite : 8 Mo).')
    raw=source.read_bytes()
    if re.search(br'<!\s*(?:DOCTYPE|ENTITY)',raw,re.I):
        raise ValueError('Les déclarations externes ne sont pas autorisées dans la BOM XML.')
    try:root=ET.fromstring(raw)
    except ET.ParseError as exc:raise ValueError('BOM XML invalide : '+str(exc)) from exc
    if root.tag!='export' or root.find('components') is None:
        raise ValueError('Format attendu : export XML KiCad avec <components><comp ref="…">.')
    by_ref={fp.GetReference():fp for fp in footprints}
    found={};ignored=0;invalid=0
    for comp in root.findall('./components/comp'):
        ref=(comp.get('ref') or '').strip()
        fp=by_ref.get(ref)
        if fp is None:ignored+=1;continue
        xml_value=(comp.findtext('value') or '').strip()
        xml_foot=(comp.findtext('footprint') or '').strip().split(':')[-1]
        if (xml_value and xml_value.casefold()!=fp.GetValue().strip().casefold()) or (xml_foot and xml_foot.casefold()!=str(fp.GetFPID().GetLibItemName()).casefold()):
            ignored+=1;continue
        numbers=[]
        for field in comp.findall('./fields/field')+comp.findall('./property'):
            if normalize_name(field.get('name')) in FIELD_NAMES:
                number=(field.get('value') or field.text or '').strip().upper()
                if PART.fullmatch(number):numbers.append(number)
                elif number:invalid+=1
        if len(set(numbers))==1:found[ref]=numbers[0]
        elif len(set(numbers))>1:invalid+=1
    return found,ignored,invalid

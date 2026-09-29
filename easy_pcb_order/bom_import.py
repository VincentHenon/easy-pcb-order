"""Lenient BOM and CPL readers for KiCad, JLCPCB and spreadsheet exports."""
import csv
from pathlib import Path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile

PART=re.compile(r'^C[1-9][0-9]*$',re.I)
REF_SPLIT=re.compile(r'[,;\s]+')
BOM_REF={'designator','designators','ref','refs','refdes','referencedesignator','referencedesignators','reference','references'}
BOM_PART={'lcsc','lcscpart','lcscpartnumber','jlcpcb','jlcpcbpart','jlcpcbpartnumber','jlcpcbpartoptional','supplierpartnumber','partnumber'}
CPL_X={'midx','x','xmm','centerx','centrex'}
CPL_Y={'midy','y','ymm','centery','centrey'}
CPL_ROT={'rotation','rotate','angle'}
CPL_LAYER={'layer','side','boardside'}

def normalize_name(name):return re.sub(r'[^a-z0-9]','',(str(name or '')).lower())
def refs(value):return [item.strip() for item in REF_SPLIT.split(str(value or '').strip()) if item.strip()]
def clean(value):return str(value or '').strip()

def text_rows(path):
    raw=Path(path).read_text(encoding='utf-8-sig')
    dialect=csv.Sniffer().sniff(raw[:4096],delimiters=',;\t|')
    return list(csv.reader(raw.splitlines(),dialect))

def xlsx_rows(path):
    ns='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    rel='{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
    with zipfile.ZipFile(path) as archive:
        shared=[]
        if 'xl/sharedStrings.xml' in archive.namelist():
            root=ET.fromstring(archive.read('xl/sharedStrings.xml'))
            for si in root.findall(ns+'si'):shared.append(''.join(node.text or '' for node in si.iter(ns+'t')))
        workbook=ET.fromstring(archive.read('xl/workbook.xml'))
        sheet=workbook.find('.//'+ns+'sheet')
        if sheet is None:raise ValueError('Le classeur ne contient aucune feuille.')
        relid=sheet.get(rel+'id')
        relationships=ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
        target=next((node.get('Target') for node in relationships if node.get('Id')==relid),None)
        if not target:raise ValueError('Feuille XLSX introuvable.')
        target='xl/'+target.lstrip('/') if not target.startswith('xl/') else target
        root=ET.fromstring(archive.read(target));rows=[]
        for row in root.findall('.//'+ns+'sheetData/'+ns+'row'):
            values=[]
            for cell in row.findall(ns+'c'):
                letters=re.match(r'[A-Z]+',cell.get('r','A1')).group(0);index=0
                for letter in letters:index=index*26+ord(letter)-64
                while len(values)<index:values.append('')
                value=cell.findtext(ns+'v','')
                if cell.get('t')=='s' and value.isdigit():value=shared[int(value)]
                elif cell.get('t')=='inlineStr':value=''.join(node.text or '' for node in cell.iter(ns+'t'))
                values[index-1]=value
            rows.append(values)
    return rows

def ods_rows(path):
    table='{urn:oasis:names:tc:opendocument:xmlns:table:1.0}';text='{urn:oasis:names:tc:opendocument:xmlns:text:1.0}'
    with zipfile.ZipFile(path) as archive:root=ET.fromstring(archive.read('content.xml'))
    table_node=root.find('.//'+table+'table')
    if table_node is None:return []
    rows=[]
    for row in table_node.findall(table+'table-row'):
        values=[]
        for item in row.findall(table+'table-cell'):
            values.extend([''.join(node.text or '' for node in item.iter(text+'p'))]*int(item.get(table+'number-columns-repeated','1')))
        rows.append(values)
    return rows

def xml_rows(path):
    raw=Path(path).read_bytes()
    if re.search(br'<!\s*(?:DOCTYPE|ENTITY)',raw,re.I):raise ValueError('Les déclarations externes ne sont pas autorisées dans le XML.')
    try:root=ET.fromstring(raw)
    except ET.ParseError as exc:raise ValueError('XML invalide : '+str(exc)) from exc
    if root.tag!='export' or root.find('components') is None:raise ValueError('XML non reconnu. Utilise une BOM KiCad, CSV ou XLSX.')
    rows=[['Designator','Value','Footprint','LCSC Part #']]
    for comp in root.findall('./components/comp'):
        number=''
        for field in comp.findall('./fields/field')+comp.findall('./property'):
            if normalize_name(field.get('name')) in BOM_PART:number=field.get('value') or field.text or ''
        rows.append([comp.get('ref',''),comp.findtext('value',''),comp.findtext('footprint',''),number])
    return rows

def read_rows(path):
    suffix=Path(path).suffix.lower()
    if suffix in ('.csv','.tsv','.txt'):return text_rows(path)
    if suffix=='.xlsx':return xlsx_rows(path)
    if suffix=='.ods':return ods_rows(path)
    if suffix=='.xml':return xml_rows(path)
    if suffix=='.numbers':
        with tempfile.TemporaryDirectory(prefix='easy-pcb-order-numbers-') as folder:
            output=str(Path(folder)/'table.xlsx')
            script='on run argv\n tell application "Numbers"\n  open (POSIX file (item 1 of argv))\n  delay 1\n  export front document to (POSIX file (item 2 of argv)) as Microsoft Excel\n  close front document saving no\n end tell\nend run'
            try:result=subprocess.run(['osascript','-e',script,str(path),output],capture_output=True,text=True,timeout=40)
            except OSError as exc:raise ValueError('Import Numbers disponible uniquement sur macOS avec Numbers installé.') from exc
            if result.returncode or not Path(output).is_file():raise ValueError('Numbers n’a pas pu exporter ce document : '+(result.stderr or result.stdout).strip()[:180])
            return xlsx_rows(output)
    raise ValueError('Format pris en charge : XLSX, CSV, TSV, ODS ou XML.')

def columns(rows):
    for index,row in enumerate(rows[:20]):
        names=[normalize_name(value) for value in row]
        if any(name in BOM_REF or name in CPL_X or name in CPL_Y for name in names):return index,{name:i for i,name in enumerate(names) if name}
    raise ValueError('En-têtes de colonnes introuvables.')
def column(mapping,names):return next((mapping[name] for name in names if name in mapping),None)
def cell(row,index):return clean(row[index]) if index is not None and index<len(row) else ''

def parse_bom(path,footprints):
    rows=read_rows(path);header,mapping=columns(rows);ref_col=column(mapping,BOM_REF);part_col=column(mapping,BOM_PART)
    if ref_col is None or part_col is None:raise ValueError('La BOM doit contenir Designator/Ref et LCSC Part # ou JLCPCB Part #.')
    allowed={fp.GetReference() for fp in footprints};found={};ignored=invalid=0
    for row in rows[header+1:]:
        number=cell(row,part_col).upper()
        if not number:continue
        if not PART.fullmatch(number):invalid+=1;continue
        for ref in refs(cell(row,ref_col)):
            if ref in allowed:found[ref]=number
            else:ignored+=1
    return found,ignored,invalid

def parse_cpl(path,footprints):
    rows=read_rows(path);header,mapping=columns(rows)
    ref_col=column(mapping,BOM_REF);x_col=column(mapping,CPL_X);y_col=column(mapping,CPL_Y);rot_col=column(mapping,CPL_ROT);layer_col=column(mapping,CPL_LAYER)
    if None in (ref_col,x_col,y_col,rot_col,layer_col):raise ValueError('Le CPL doit contenir Designator, Mid X, Mid Y, Rotation et Layer.')
    allowed={fp.GetReference() for fp in footprints};found={};ignored=invalid=0
    for row in rows[header+1:]:
        try:x=float(cell(row,x_col).replace(',','.'));y=float(cell(row,y_col).replace(',','.'));rotation=float(cell(row,rot_col).replace(',','.'))%360
        except ValueError:invalid+=1;continue
        layer=cell(row,layer_col).casefold()
        if layer in ('top','t','front','f'):layer='Top'
        elif layer in ('bottom','bot','b','back'):layer='Bottom'
        else:invalid+=1;continue
        for ref in refs(cell(row,ref_col)):
            if ref in allowed:found[ref]=(x,y,rotation,layer)
            else:ignored+=1
    return found,ignored,invalid

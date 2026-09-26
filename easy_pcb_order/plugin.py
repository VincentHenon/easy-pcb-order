"""Export independent closed Edge.Cuts contours from one KiCad PCB.
KiCad 9/10 legacy pcbnew action plugin. Never changes the open board.
"""
import csv
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zipfile
import wx
import pcbnew
from .parts import PartsDialog, read_assignments, write_assignments
from . import geometry

EDGE = pcbnew.Edge_Cuts
PRESETS = {
    'JLCPCB': {'bom': True, 'cpl': True},
    'PCBWay': {'bom': True, 'cpl': True},
    'Generic Gerber': {'bom': False, 'cpl': False},
}
COPPER = ['F.Cu', 'In1.Cu', 'In2.Cu', 'In3.Cu', 'In4.Cu', 'In5.Cu', 'In6.Cu',
          'In7.Cu', 'In8.Cu', 'In9.Cu', 'In10.Cu', 'In11.Cu', 'In12.Cu',
          'In13.Cu', 'In14.Cu', 'In15.Cu', 'In16.Cu', 'In17.Cu', 'In18.Cu',
          'In19.Cu', 'In20.Cu', 'In21.Cu', 'In22.Cu', 'In23.Cu', 'In24.Cu',
          'In25.Cu', 'In26.Cu', 'In27.Cu', 'In28.Cu', 'In29.Cu', 'In30.Cu', 'B.Cu']
OTHER = ['F.Paste', 'B.Paste', 'F.SilkS', 'B.SilkS', 'F.Mask', 'B.Mask', 'Edge.Cuts']
TOL = pcbnew.FromMM(0.01)

def xy(p):
    return (p.x, p.y)

def bb_center(obj):
    b = obj.GetBoundingBox()
    return ((b.GetLeft() + b.GetRight()) / 2, (b.GetTop() + b.GetBottom()) / 2)

def connected_contours(board):
    edges=[d for d in board.GetDrawings() if d.GetLayer()==EDGE]
    if not edges:
        raise ValueError('Aucun contour Edge.Cuts trouvé.')
    paths=[]
    step=pcbnew.FromMM(0.05)
    for d in edges:
        kind=d.GetShapeStr().lower()
        if 'rect' in kind:
            b=d.GetBoundingBox()
            pts=[(b.GetLeft(),b.GetTop()),(b.GetRight(),b.GetTop()),
                 (b.GetRight(),b.GetBottom()),(b.GetLeft(),b.GetBottom())]
            paths.append((pts+[pts[0]],d))
        elif 'circle' in kind:
            center=xy(d.GetCenter())
            radius=d.GetRadius()
            pts=geometry.circle_points(center,radius,step)
            paths.append((pts+[pts[0]],d))
        elif 'arc' in kind:
            paths.append((geometry.arc_points(xy(d.GetStart()),xy(d.GetArcMid()),xy(d.GetEnd()),step),d))
        elif 'segment' in kind or 'line' in kind:
            paths.append(([xy(d.GetStart()),xy(d.GetEnd())],d))
        else:
            raise ValueError('Contour Edge.Cuts non pris en charge : %s.' % kind)
    try:
        return geometry.classify(geometry.stitch(paths,TOL))
    except ValueError as e:
        raise ValueError(str(e)+' (coordonnées internes KiCad)') from e

def owner(point,contours):
    return geometry.owner(point,contours)

def item_owner(obj, contours):
    p = bb_center(obj)
    idx = owner(p, contours)
    b = obj.GetBoundingBox()
    # Items may extend beyond an outline (connectors, ref text); disallow crossing for copper and tracks only.
    if isinstance(obj, pcbnew.PCB_TRACK) or isinstance(obj, pcbnew.ZONE):
        for corner in [(b.GetLeft(),b.GetTop()), (b.GetRight(),b.GetTop()),
                       (b.GetRight(),b.GetBottom()), (b.GetLeft(),b.GetBottom())]:
            if not geometry.contains(corner,contours[idx]['poly']) or any(geometry.contains(corner,hole) for hole in contours[idx]['holes']):
                raise ValueError('Piste ou zone traversant le contour près de %.2f, %.2f mm.' %
                                 (pcbnew.ToMM(p[0]), pcbnew.ToMM(p[1])))
    return idx

def slug(name):
    value = re.sub(r'[^a-zA-Z0-9_-]+', '-', name.strip()).strip('-_')
    if not value:
        raise ValueError('Chaque carte doit avoir un nom contenant des lettres ou chiffres ASCII.')
    return value

def fields(fp):
    result = {}
    if hasattr(fp, 'GetFields'):
        for f in fp.GetFields():
            result[f.GetName().strip().lower()] = f.GetText().strip()
    return result

def part_number(fp):
    f = fields(fp)
    return next((f[k] for k in ('lcsc part #', 'lcsc', 'jlcpcb part #', 'jlcpcb') if f.get(k)), '')

def exclude(fp):
    return (hasattr(fp, 'GetExcludedFromBOM') and fp.GetExcludedFromBOM()) or (hasattr(fp, 'GetDNP') and fp.GetDNP())

def export_assembly(board, path, preset, assignments):
    bom = []
    pos = []
    for fp in board.GetFootprints():
        if exclude(fp):
            continue
        ref = fp.GetReference()
        value = fp.GetValue()
        footprint = str(fp.GetFPID().GetLibItemName())
        number = assignments.get(ref, part_number(fp))
        if number:
            bom.append((ref, footprint, value, number))
        if fp.GetAttributes() & pcbnew.FP_SMD and number:
            p = fp.GetPosition()
            pos.append((ref, '%.4f' % pcbnew.ToMM(p.x), '%.4f' % pcbnew.ToMM(p.y),
                        '%.2f' % (fp.GetOrientationDegrees() % 360),
                        'Bottom' if fp.IsFlipped() else 'Top'))
    if preset == 'JLCPCB':
        header = ['Designator','Footprint','Quantity','Value','LCSC Part #']
        rows = [[r,foot,'1',value,num] for r,foot,value,num in bom]
    else:
        header = ['Designator','Footprint','Quantity','Value','Manufacturer Part Number']
        rows = [[r,foot,'1',value,num] for r,foot,value,num in bom]
    with (path/'bom.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.writer(f); w.writerow(header); w.writerows(rows)
    with (path/'positions.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.writer(f); w.writerow(['Designator','Mid X','Mid Y','Rotation','Layer']); w.writerows(pos)
    return len(bom), len(pos)

def run_cli(args):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('%s\n%s' % (' '.join(args), (result.stderr or result.stdout).strip()))

def output_one(source, target, contours, index, entry, preset, cli, assignments):
    target.mkdir(parents=True)
    temp = target / (slug(entry['name']) + '.kicad_pcb')
    board = pcbnew.LoadBoard(str(source))
    kept_edges = {str(edge.m_Uuid) for edge in contours[index]['edges']}
    # Evaluate ownership before mutation; the original board remains untouched.
    removals = []
    for collection in (board.GetFootprints(), board.GetTracks(), board.Zones(), board.GetDrawings()):
        for obj in list(collection):
            if obj.GetLayer() == EDGE and str(obj.m_Uuid) in kept_edges:
                continue
            if obj.GetLayer() == EDGE:
                removals.append(obj)
            elif item_owner(obj, contours) != index:
                removals.append(obj)
    for obj in removals:
        board.Remove(obj)
    active = int(entry['layers'])
    if active not in (2,4,6,8,10,12,14,16,18,20,22,24,26,28,30,32):
        raise ValueError('Nombre de couches invalide pour %s.' % entry['name'])
    # Changing layer stacks is not safe through this API. Reject inconsistent requests.
    configured = board.GetCopperLayerCount()
    if active != configured:
        raise ValueError('%s : %s couches demandées, mais le PCB source est configuré sur %s. La pile de couches doit être modifiée dans KiCad avant export.' % (entry['name'], active, configured))
    if not pcbnew.SaveBoard(str(temp), board):
        raise RuntimeError('Échec de la sauvegarde du PCB temporaire : %s' % temp)
    layers = [l for l in COPPER+OTHER if board.IsLayerEnabled(board.GetLayerID(l))]
    gerbers = target / 'gerbers'
    gerbers.mkdir()
    run_cli([cli,'pcb','export','gerbers','-l',','.join(layers),'-o',str(gerbers),str(temp)])
    run_cli([cli,'pcb','export','drill','-o',str(gerbers),str(temp)])
    if not list(gerbers.iterdir()):
        raise RuntimeError('Aucun fichier de fabrication généré : %s' % entry['name'])
    with zipfile.ZipFile(target/'gerbers.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(gerbers.iterdir()):
            archive.write(file,file.name)
    shutil.rmtree(gerbers)
    counts = (0,0)
    if PRESETS[preset]['bom'] and not entry['panel']:
        counts = export_assembly(board,target,preset,assignments)
    (target/'manifest.json').write_text(json.dumps({'name':entry['name'],'front_panel':entry['panel'],
         'layers':active,'preset':preset,'bom_parts_with_number':counts[0],
         'placed_smd_with_number':counts[1], 'source':str(source)},indent=2),encoding='utf-8')
    temp.unlink()

class BoardDialog(wx.Dialog):
    def __init__(self,parent,contours,board):
        super().__init__(parent,title='easy-pcb-order — cartes à exporter',size=(700,440))
        root=wx.BoxSizer(wx.VERTICAL)
        root.Add(wx.StaticText(self,label='Chaque contour extérieur Edge.Cuts est une carte ; les contours intérieurs sont ses découpes.'),0,wx.ALL,10)
        grid=wx.FlexGridSizer(len(contours)+1,4,7,9)
        for title in ('Contour','Nom du design','Couches','Front panel'):
            grid.Add(wx.StaticText(self,label=title))
        self.rows=[]
        n=board.GetCopperLayerCount()
        for i,c in enumerate(contours):
            size=(max(p[0] for p in c['poly'])-min(p[0] for p in c['poly']),max(p[1] for p in c['poly'])-min(p[1] for p in c['poly']))
            grid.Add(wx.StaticText(self,label='%d — %.1f × %.1f mm' % (i+1,pcbnew.ToMM(size[0]),pcbnew.ToMM(size[1]))))
            name=wx.TextCtrl(self,value='PCB_%d' % (i+1)); grid.Add(name,1,wx.EXPAND)
            layers=wx.Choice(self,choices=[str(k) for k in range(2,33,2)]); layers.SetStringSelection(str(n)); grid.Add(layers)
            panel=wx.CheckBox(self); grid.Add(panel)
            self.rows.append((name,layers,panel))
        grid.AddGrowableCol(1,1)
        root.Add(grid,0,wx.EXPAND|wx.ALL,12)
        line=wx.BoxSizer(wx.HORIZONTAL)
        line.Add(wx.StaticText(self,label='Usine / preset :'),0,wx.ALIGN_CENTER_VERTICAL|wx.RIGHT,8)
        self.preset=wx.Choice(self,choices=list(PRESETS)); self.preset.SetSelection(0); line.Add(self.preset)
        root.Add(line,0,wx.ALL,12)
        root.Add(wx.StaticText(self,label='Le fichier source est conservé. Les exports sont placés dans un dossier séparé.'),0,wx.ALL,10)
        root.Add(self.CreateButtonSizer(wx.OK|wx.CANCEL),0,wx.EXPAND|wx.ALL,10)
        self.SetSizer(root)
    def entries(self):
        result=[{'name':a.GetValue().strip(),'layers':int(b.GetStringSelection()),'panel':c.GetValue()} for a,b,c in self.rows]
        names=[slug(e['name']).lower() for e in result]
        if len(set(names))!=len(names):
            raise ValueError('Les noms des cartes doivent être distincts.')
        return result

class MultiPCBExporter(pcbnew.ActionPlugin):
    def defaults(self):
        self.name='easy-pcb-order'
        self.category='Fabrication'
        self.description='Sépare les contours Edge.Cuts et exporte Gerbers, BOM et placement'
        self.show_toolbar_button=True
    def Run(self):
        try:
            current=pcbnew.GetBoard()
            source=Path(current.GetFileName())
            if not source.is_file():
                raise ValueError('Enregistre le PCB avant de lancer le plugin.')
            # Read the saved file: avoid silently exporting stale content.
            if hasattr(current, 'IsModified') and current.IsModified():
                raise ValueError('Enregistre le PCB dans KiCad avant l’export.')
            board=pcbnew.LoadBoard(str(source))
            contours=connected_contours(board)
            with BoardDialog(None,contours,board) as dialog:
                if dialog.ShowModal()!=wx.ID_OK:
                    return
                entries=dialog.entries()
                preset=dialog.preset.GetStringSelection()
            assignments=read_assignments(source)
            footprints=[fp for fp in board.GetFootprints() if not exclude(fp) and owner(bb_center(fp),contours) < len(contours) and not entries[owner(bb_center(fp),contours)]['panel']]
            if PRESETS[preset]['bom'] and footprints:
                for fp in footprints:
                    assignments.setdefault(fp.GetReference(),part_number(fp))
                with PartsDialog(None,footprints,assignments) as part_dialog:
                    if part_dialog.ShowModal()!=wx.ID_OK: return
                    assignments=part_dialog.mapping
            cli=shutil.which('kicad-cli')
            if not cli:
                raise ValueError('kicad-cli introuvable. Ajoute le dossier bin de KiCad au PATH.')
            base=source.parent / (source.stem+'-fabrication')
            if base.exists():
                raise ValueError('Le dossier de sortie existe déjà : %s. Déplace-le ou renomme-le avant un nouvel export.' % base)
            # Stage everything to avoid partially published results.
            with tempfile.TemporaryDirectory(prefix='multipcb-') as tmp:
                stage=Path(tmp)/base.name
                for i,entry in enumerate(entries):
                    output_one(source,stage/slug(entry['name']),contours,i,entry,preset,cli,assignments)
                shutil.move(str(stage),str(base))
            if PRESETS[preset]['bom']:
                write_assignments(source,assignments)
            wx.MessageBox('%d cartes exportées dans :\n%s' % (len(entries),base),'easy-pcb-order',wx.OK|wx.ICON_INFORMATION)
        except Exception as e:
            wx.MessageBox(str(e),'easy-pcb-order — export interrompu',wx.OK|wx.ICON_ERROR)

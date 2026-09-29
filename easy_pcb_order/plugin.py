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
from .parts import PART, PartsDialog, ImportDialog, ReviewDialog, apply_imported_groups, part_key, read_assignments, write_assignments, read_stock_notes, write_stock_notes
from . import geometry, wizard
from .preview import find_cli

EDGE = pcbnew.Edge_Cuts
PRESETS = {
    'JLCPCB': {'bom': True, 'cpl': True},
    'PCBWay': {'bom': True, 'cpl': True},
    'Autre fabricant (Gerber + BOM + placement)': {'bom': True, 'cpl': True},
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

def item_owner(obj,contours):
    b=obj.GetBoundingBox()
    center=bb_center(obj)
    if isinstance(obj,pcbnew.PCB_TRACK):
        points=[xy(obj.GetStart()),xy(obj.GetEnd())]
        idx=owner(points[0],contours)
        if any(owner(p,contours)!=idx for p in points[1:]):
            raise ValueError('Piste traversant deux cartes ou une découpe.')
        return idx
    if isinstance(obj,pcbnew.ZONE):
        x0,x1=b.GetLeft(),b.GetRight();y0,y1=b.GetTop(),b.GetBottom()
        samples=[center]+[(x0+(x1-x0)*x,y0+(y1-y0)*y)
                          for x,y in ((.25,.25),(.75,.25),(.25,.75),(.75,.75))]
        matches=[]
        for sample in samples:
            try:matches.append(owner(sample,contours))
            except ValueError:pass  # A large zone may surround a cutout.
        if not matches or len(set(matches))!=1:
            raise ValueError('Zone de cuivre impossible à attribuer à une seule carte.')
        return matches[0]
    return owner(center,contours)

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

def export_assembly(board, path, preset, assignments, placements=None):
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
            override=(placements or {}).get(ref)
            if override:x,y,rotation,layer=override
            else:
                p=fp.GetPosition();x,y=pcbnew.ToMM(p.x),pcbnew.ToMM(p.y)
                rotation=fp.GetOrientationDegrees()%360;layer='Bottom' if fp.IsFlipped() else 'Top'
            pos.append((ref, '%.4f' % x, '%.4f' % y,'%.2f' % rotation,layer))
    if preset == 'JLCPCB':
        header=['Designator','Footprint','Quantity','Value','LCSC Part #']
        rows=[[r,foot,'1',value,num] for r,foot,value,num in bom]
        pos_header=['Designator','Mid X','Mid Y','Rotation','Layer']
    elif preset == 'PCBWay':
        header=['Line#','Quantity Per Part Number','Reference Designator','Part Number',
                'Part Description','Package','Type','Manufacturers Name',
                'Manufacturers Part Number','Distributors Part Number']
        by_ref={fp.GetReference():fp for fp in board.GetFootprints()}
        rows=[]
        for line,(ref,foot,value,num) in enumerate(bom,1):
            fp=by_ref[ref]
            f=fields(fp)
            rows.append([line,1,ref,f.get('mpn','') or num,value,foot,
                         'SMD' if fp.GetAttributes() & pcbnew.FP_SMD else 'THT',
                         f.get('manufacturer',''),f.get('mpn',''),num])
        pos_header=['RefDes','X (mm)','Y (mm)','Rotation','Side']
    else:
        header=['Reference','Quantity','Value','Footprint','Supplier Part Number']
        rows=[[r,1,value,foot,num] for r,foot,value,num in bom]
        pos_header=['RefDes','X (mm)','Y (mm)','Rotation','Side']
    with (path/'bom.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.writer(f); w.writerow(header); w.writerows(rows)
    with (path/'positions.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.writer(f); w.writerow(pos_header); w.writerows(pos)
    return len(bom), len(pos)

def run_cli(args):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('%s\n%s' % (' '.join(args), (result.stderr or result.stdout).strip()))

def output_one(source, target, contours, index, entry, preset, cli, assignments, placements=None):
    target.mkdir(parents=True)
    temp = target / (slug(entry['name']) + '.kicad_pcb')
    # pcbnew.LoadBoard() may return KiCad's live board when given the exact
    # path already open in the editor.  Always copy first: every removal below
    # must happen on a distinct file and never on the user's editor board.
    shutil.copy2(source,temp)
    board = pcbnew.LoadBoard(str(temp))
    kept_edges = {str(edge.m_Uuid) for edge in contours[index]['edges']}
    # Evaluate ownership before mutation; the original board remains untouched.
    removals = []
    # Drawings outside an outline are commonly dimensions, notes, or a panel
    # legend.  They are not manufacturable board content, so omit them from a
    # split board instead of refusing the whole export.  Copper remains strict:
    # an out-of-board track or zone is almost certainly a real design error.
    for kind,collection in (('footprint',board.GetFootprints()),
                            ('copper',board.GetTracks()),
                            ('copper',board.Zones()),
                            ('drawing',board.GetDrawings())):
        for obj in list(collection):
            if obj.GetLayer() == EDGE and str(obj.m_Uuid) in kept_edges:
                continue
            if obj.GetLayer() == EDGE:
                removals.append(obj)
                continue
            try:
                object_index=item_owner(obj,contours)
            except ValueError:
                if kind in ('footprint','drawing'):
                    removals.append(obj)
                    continue
                raise
            if object_index != index:
                removals.append(obj)
    for obj in removals:
        board.Remove(obj)
    active = int(entry['layers'])
    if active not in (2,4,6,8,10,12,14,16,18,20,22,24,26,28,30,32):
        raise ValueError('Nombre de couches invalide pour %s.' % entry['name'])
    # Layer usage was validated before modifying this temporary copy.
    configured = board.GetCopperLayerCount()
    if active > configured:
        raise ValueError('%s : %s couches demandées mais %s dans le source.' % (entry['name'],active,configured))
    if active < configured:
        board.SetCopperLayerCount(active)
    if not pcbnew.SaveBoard(str(temp), board):
        raise RuntimeError('Échec de la sauvegarde du PCB temporaire : %s' % temp)
    if pcbnew.LoadBoard(str(temp)).GetCopperLayerCount()!=active:
        raise RuntimeError('La pile de couches de %s n’a pas été enregistrée correctement.' % entry['name'])
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
        counts = export_assembly(board,target,preset,assignments,placements)
    (target/'manifest.json').write_text(json.dumps({'name':entry['name'],'front_panel':entry['panel'],
         'layers':active,'preset':preset,'bom_parts_with_number':counts[0],
         'placed_smd_with_number':counts[1], 'source':str(source)},indent=2),encoding='utf-8')
    temp.unlink()

def validate_design_layers(board,index,contours,target):
    source=board.GetCopperLayerCount()
    if target>source or target<2 or target%2:
        raise ValueError('Le design %d demande %d couches mais le fichier source en a %d.' % (index+1,target,source))
    if target==source:return
    removed=['In%d.Cu' % n for n in range(target-1,source-1)]
    for obj in list(board.GetTracks())+list(board.Zones())+list(board.GetDrawings()):
        if obj.GetLayer()==EDGE:continue
        try:
            if item_owner(obj,contours)!=index:continue
        except ValueError:
            if isinstance(obj,(pcbnew.PCB_TRACK,pcbnew.ZONE)):
                raise
            continue
        if isinstance(obj,pcbnew.PCB_VIA):
            if obj.GetViaType()!=pcbnew.VIATYPE_THROUGH and not obj.HasValidLayerPair(target):
                raise ValueError('Design %d : un via borgne ou enterré utilise une couche absente de la pile à %d couches.' % (index+1,target))
            continue  # Un via traversant reste valide en deux couches.
        if any(obj.IsOnLayer(board.GetLayerID(name)) for name in removed):
            raise ValueError('Design %d : cuivre présent sur %s. Impossible de réduire à %d couches.' %
                             (index+1,', '.join(removed),target))

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
            mode=wizard.project_mode()
            if mode is None:return
            board=pcbnew.LoadBoard(str(source))
            contours=connected_contours(board)
            if not wizard.verify_detection(contours,board,mode):return
            with wizard.LayerDialog(contours,board) as dialog:
                if dialog.ShowModal()!=wx.ID_OK:return
                layer_settings=dialog.values()
            for i,(layers,_) in enumerate(layer_settings):
                validate_design_layers(board,i,contours,layers)
            with wizard.NameDialog(contours,board) as dialog:
                if dialog.ShowModal()!=wx.ID_OK:return
                names=dialog.values()
            if len(set(slug(name).lower() for name in names))!=len(names):
                raise ValueError('Les noms des designs doivent être distincts.')
            entries=[{'name':name,'layers':layers,'panel':panel}
                     for name,(layers,panel) in zip(names,layer_settings)]
            assignments=read_assignments(source)
            imported_cpl={}
            eligible=[]
            for fp in current.GetFootprints():
                if exclude(fp):
                    continue
                try:
                    design_index=owner(bb_center(fp),contours)
                except ValueError:
                    # Library footprints, notes and panel tooling may be kept
                    # outside every closed design.  They cannot belong to an
                    # assembly export, so leave them out of the assignment UI.
                    continue
                if not entries[design_index]['panel']:
                    eligible.append(fp)
            eligible.sort(key=lambda fp:fp.GetReference())
            for fp in eligible:
                if PART.fullmatch(part_number(fp)):
                    assignments.setdefault(fp.GetReference(),part_number(fp))
            if eligible:
                with ImportDialog(None,eligible) as import_dialog:
                    if import_dialog.ShowModal()!=wx.ID_OK:return
                    imported=import_dialog.imported
                    imported_cpl=import_dialog.cpl
                assignments,imported_keys=apply_imported_groups(eligible,assignments,imported)
                to_assign=[fp for fp in eligible if part_key(fp) not in imported_keys]
                if to_assign:
                    with PartsDialog(None,to_assign,assignments) as dialog:
                        try:
                            if dialog.ShowModal()!=wx.ID_OK:return
                            assignments=dialog.mapping
                        finally:
                            dialog.clear_highlight()
                            dialog.stop_preview()
            if eligible:
                with ReviewDialog(None,[[fp] for fp in eligible],assignments,read_stock_notes(source)) as dialog:
                    if dialog.ShowModal()!=wx.ID_OK:return
                    assignments=dialog.mapping
                    stock_notes=dialog.stock_notes
            preset=wizard.factory_choice()
            if preset is None:return
            cli=find_cli()
            if not cli:raise ValueError('kicad-cli introuvable. Vérifie que KiCad est installé dans Applications puis relance le PCB Editor.')
            base=source.parent / (source.stem+'-fabrication')
            if base.exists():
                raise ValueError('Le dossier de sortie existe déjà : %s. Déplace-le ou renomme-le avant un nouvel export.' % base)
            # Stage everything to avoid partially published results.
            with tempfile.TemporaryDirectory(prefix='easy-pcb-order-') as tmp:
                stage=Path(tmp)/base.name
                for i,entry in enumerate(entries):
                    output_one(source,stage/slug(entry['name']),contours,i,entry,preset,cli,assignments,imported_cpl)
                shutil.move(str(stage),str(base))
            if PRESETS[preset]['bom']:
                write_assignments(source,assignments)
                if eligible:write_stock_notes(source,stock_notes)
            wx.MessageBox('%d cartes exportées dans :\n%s' % (len(entries),base),'easy-pcb-order',wx.OK|wx.ICON_INFORMATION)
        except Exception as e:
            wx.MessageBox(str(e),'easy-pcb-order — export interrompu',wx.OK|wx.ICON_ERROR)

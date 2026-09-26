"""Official LCSC partner search and user-reviewed part assignments."""
import hashlib
from datetime import datetime
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
import wx
from . import preview
from .bom_import import parse_kicad_xml

PART = re.compile(r'^C[1-9][0-9]*$', re.I)

def part_key(fp):
    """Group equivalent designators by family, displayed value and footprint."""
    ref=fp.GetReference()
    family=re.match(r'[A-Za-z]+',ref)
    family=family.group(0).upper() if family else ref.upper()
    value=re.sub(r'\s+','',fp.GetValue().casefold()).replace('ω','ohm').replace('Ω','ohm')
    footprint=str(fp.GetFPID().GetLibItemName()).casefold()
    try:
        import pcbnew
        assembly='SMD' if fp.GetAttributes() & pcbnew.FP_SMD else 'THT'
    except (ImportError,AttributeError):
        assembly='unknown'
    return family,value,footprint,assembly

def group_footprints(footprints, assignments):
    """Existing per-reference assignments do not prevent grouping."""
    groups={}
    for fp in footprints:
        key=part_key(fp)
        groups.setdefault(key,[]).append(fp)
    return list(groups.values())

def apply_imported_groups(footprints, assignments, imported):
    """Complete groups with a single imported code; leave conflicts for manual review."""
    merged=dict(assignments)
    merged.update(imported)
    completed=set()
    for group in group_footprints(footprints,merged):
        codes={imported[fp.GetReference()] for fp in group if fp.GetReference() in imported}
        if len(codes)==1:
            code=next(iter(codes))
            for fp in group:merged[fp.GetReference()]=code
            completed.add(part_key(group[0]))
    return merged,completed

def search_lcsc(term):
    key, secret = os.getenv('LCSC_API_KEY',''), os.getenv('LCSC_API_SECRET','')
    if not key or not secret:
        raise ValueError('Recherche API LCSC : définir LCSC_API_KEY et LCSC_API_SECRET (accès partenaire LCSC).')
    nonce = secrets.token_hex(8)
    stamp = str(int(time.time()))
    raw = 'key=%s&nonce=%s&secret=%s&timestamp=%s' % (key,nonce,secret,stamp)
    signature = hashlib.sha1(raw.encode('utf-8')).hexdigest()
    params = {'key':key,'nonce':nonce,'timestamp':stamp,'signature':signature,
              'keyword':term,'match_type':'fuzzy','current_page':1,'page_size':30,'is_available':'true'}
    url = 'https://ips.lcsc.com/rest/wmsc2agent/search/product?' + urllib.parse.urlencode(params)
    with urllib.request.urlopen(urllib.request.Request(url,headers={'Accept':'application/json'}),timeout=12) as response:
        data = json.load(response)
    # Response wrappers vary between LCSC API revisions; only surface actual product records.
    def records(obj):
        if isinstance(obj,list):
            for item in obj:
                yield from records(item)
        elif isinstance(obj,dict):
            if any(k in obj for k in ('lcsc_part_number','product_number','productCode','product_code')):
                yield obj
            else:
                for value in obj.values():
                    if isinstance(value,(list,dict)):
                        yield from records(value)
    found=[]
    for item in records(data):
        num=next((str(item[k]) for k in ('lcsc_part_number','product_number','productCode','product_code') if item.get(k)), '')
        if PART.fullmatch(num):
            label=next((str(item[k]) for k in ('product_name','productName','product_model','productModel','mpn') if item.get(k)), '')
            stock=next((str(item[k]) for k in ('stock','quantity','available_quantity') if item.get(k) is not None), '?')
            found.append((num,label,stock))
    return found

def read_assignments(source):
    path=Path(str(source)+'.parts.json')
    if not path.exists():
        return {}
    data=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data,dict):
        raise ValueError('Fichier des composants invalide : %s' % path)
    return data

def write_assignments(source, mapping):
    path=Path(str(source)+'.parts.json')
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(mapping,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    tmp.replace(path)

def read_stock_notes(source):
    path=Path(str(source)+'.stock.json')
    if not path.exists():return {}
    data=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data,dict):raise ValueError('Fichier de relevés de stock invalide : %s' % path)
    return data

def write_stock_notes(source, notes):
    path=Path(str(source)+'.stock.json')
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(notes,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    tmp.replace(path)

class ImportDialog(wx.Dialog):
    """Optional BOM import before any part assignment; report matches explicitly."""
    def __init__(self,parent,footprints):
        super().__init__(parent,title='easy-pcb-order  ·  Importer une BOM',size=(690,360))
        self.SetBackgroundColour(preview.BG)
        self.footprints=footprints
        self.imported={}
        root=wx.BoxSizer(wx.VERTICAL)
        title=wx.StaticText(self,label='Importer des références existantes')
        title.SetForegroundColour(preview.INK)
        font=title.GetFont();font.SetPointSize(font.GetPointSize()+5);font.SetWeight(wx.FONTWEIGHT_BOLD)
        title.SetFont(font)
        root.Add(title,0,wx.ALL,24)
        description=wx.StaticText(self,label='BOM XML exportée depuis KiCad : les champs LCSC ou JLCPCB sont associés aux références du PCB.\nUn fichier partiel convient. Les autres groupes seront proposés à l’étape suivante.')
        description.SetForegroundColour(preview.MUTED)
        root.Add(description,0,wx.LEFT|wx.RIGHT|wx.BOTTOM,24)
        root.Add(preview.ActionButton(self,'Choisir un fichier XML',self.choose,width=230),0,wx.LEFT|wx.RIGHT,24)
        self.summary=wx.StaticText(self,label='Aucun fichier sélectionné. Tu peux continuer et attribuer les références manuellement.')
        self.summary.SetForegroundColour(preview.ACCENT)
        root.Add(self.summary,0,wx.ALL,24)
        root.AddStretchSpacer()
        footer=wx.BoxSizer(wx.HORIZONTAL)
        footer.Add(preview.ActionButton(self,'Annuler',lambda event:self.EndModal(wx.ID_CANCEL),width=120),0,wx.RIGHT,12)
        footer.Add(preview.ActionButton(self,'Continuer  →',lambda event:self.EndModal(wx.ID_OK),'primary',width=180))
        root.Add(footer,0,wx.ALIGN_RIGHT|wx.ALL,24)
        self.SetSizer(root)
    def choose(self,event):
        with wx.FileDialog(self,'Choisir une BOM XML KiCad',wildcard='BOM XML (*.xml)|*.xml',style=wx.FD_OPEN|wx.FD_FILE_MUST_EXIST) as dlg:
            if dlg.ShowModal()!=wx.ID_OK:return
            path=dlg.GetPath()
        try:
            imported,ignored,invalid=parse_kicad_xml(path,self.footprints)
        except (OSError,ValueError) as exc:
            wx.MessageBox(str(exc),'Import BOM',wx.OK|wx.ICON_WARNING)
            return
        self.imported=imported
        self.summary.SetLabel('%s\n%d référence(s) LCSC trouvée(s) sur ce PCB · %d ligne(s) ignorée(s) · %d numéro(s) invalide(s).' % (
            Path(path).name,len(imported),ignored,invalid))
        self.summary.Wrap(620)
        self.Layout()

class PartsDialog(wx.Dialog):
    """One conservative group at a time, with canvas and dialog feedback."""
    def __init__(self,parent,footprints,mapping):
        super().__init__(parent,title='easy-pcb-order  ·  Choisir les composants',size=(1040,760))
        self.mapping=dict(mapping)
        self.groups=group_footprints(footprints,self.mapping)
        self.index=0
        self.highlighted=[]
        self.preview_dir=tempfile.TemporaryDirectory(prefix='easy-pcb-order-preview-')
        self.render_token=0
        self.cached_models={}
        self.rendering=False
        self.pending_model=None
        self.closed=False
        self.SetBackgroundColour(preview.BG)
        root=wx.BoxSizer(wx.VERTICAL)
        body=wx.ScrolledWindow(self,style=wx.VSCROLL)
        body.SetBackgroundColour(preview.BG)
        content=wx.BoxSizer(wx.VERTICAL)
        eyebrow=wx.StaticText(body,label='EASY PCB ORDER   /   COMPOSANTS')
        eyebrow.SetForegroundColour(preview.ACCENT)
        content.Add(eyebrow,0,wx.LEFT|wx.RIGHT|wx.TOP,24)
        self.heading=wx.StaticText(body,label='')
        self.heading.SetForegroundColour(preview.INK)
        font=self.heading.GetFont();font.SetPointSize(font.GetPointSize()+5);font.SetWeight(wx.FONTWEIGHT_BOLD)
        self.heading.SetFont(font)
        content.Add(self.heading,0,wx.LEFT|wx.RIGHT|wx.TOP|wx.BOTTOM,24)
        self.progress=preview.ProgressTrack(body)
        content.Add(self.progress,0,wx.EXPAND|wx.LEFT|wx.RIGHT|wx.BOTTOM,24)
        gallery=wx.BoxSizer(wx.HORIZONTAL)
        self.footprint_preview=preview.FootprintPreview(body)
        self.model_preview=preview.ModelPreview(body)
        gallery.Add(self.footprint_preview,1,wx.EXPAND|wx.RIGHT,12)
        gallery.Add(self.model_preview,1,wx.EXPAND)
        content.Add(gallery,0,wx.EXPAND|wx.LEFT|wx.RIGHT,24)
        self.visual=wx.StaticText(body,label='')
        self.visual.SetForegroundColour(preview.MUTED)
        content.Add(self.visual,0,wx.LEFT|wx.RIGHT|wx.TOP|wx.BOTTOM,24)
        assignment=wx.Panel(body)
        assignment.SetBackgroundColour(preview.CARD)
        block=wx.BoxSizer(wx.VERTICAL)
        label=wx.StaticText(assignment,label='NUMÉRO LCSC  ·  UN CHOIX POUR TOUT LE GROUPE')
        label.SetForegroundColour(preview.ACCENT)
        block.Add(label,0,wx.ALL,14)
        self.part=wx.TextCtrl(assignment,style=wx.TE_PROCESS_ENTER)
        self.part.SetBackgroundColour(preview.FIELD)
        self.part.SetForegroundColour(preview.INK)
        input_font=self.part.GetFont();input_font.SetPointSize(input_font.GetPointSize()+5)
        self.part.SetFont(input_font)
        self.part.SetHint('Exemple : C25804')
        self.part.Bind(wx.EVT_TEXT_ENTER,self.forward)
        block.Add(self.part,0,wx.EXPAND|wx.LEFT|wx.RIGHT|wx.BOTTOM,14)
        assignment.SetSizer(block)
        content.Add(assignment,0,wx.EXPAND|wx.LEFT|wx.RIGHT,24)
        actions=wx.BoxSizer(wx.HORIZONTAL)
        for title,handler in [('Rechercher LCSC',self.search),('Catalogue JLCPCB',self.open_site)]:
            b=preview.ActionButton(body,title,handler,width=175)
            actions.Add(b,0,wx.RIGHT,10)
        content.Add(actions,0,wx.LEFT|wx.RIGHT|wx.TOP,24)
        hint=wx.StaticText(body,label='Vérifie couleur, tension, tolérance et polarité dans la fiche fabricant avant de valider le groupe.')
        hint.SetForegroundColour(preview.MUTED)
        content.Add(hint,0,wx.ALL,24)
        body.SetSizer(content);body.SetScrollRate(0,12)
        self.body=body
        root.Add(body,1,wx.EXPAND)
        buttons=wx.BoxSizer(wx.HORIZONTAL)
        self.previous=preview.ActionButton(self,'←  Précédent',self.back,width=150)
        self.next=preview.ActionButton(self,'Suivant  →',self.forward,'primary',width=185)
        self.skip=preview.ActionButton(self,'Ne pas placer  →',self.skip_group,width=180)
        cancel=preview.ActionButton(self,'Annuler',lambda event:self.EndModal(wx.ID_CANCEL),width=120)
        buttons.Add(cancel,0,wx.RIGHT,12)
        buttons.Add(self.previous,0,wx.RIGHT,12)
        buttons.Add(self.skip,0,wx.RIGHT,12)
        buttons.Add(self.next)
        root.Add(buttons,0,wx.ALIGN_RIGHT|wx.LEFT|wx.RIGHT|wx.TOP|wx.BOTTOM,18)
        self.SetSizer(root)
        self.Bind(wx.EVT_CLOSE,self.on_close)
        wx.CallAfter(self.show_current)
    def clear_highlight(self):
        for fp in self.highlighted:
            try: fp.ClearBrightened()
            except Exception: pass
        if self.highlighted:
            try:
                import pcbnew
                pcbnew.Refresh()
            except Exception: pass
        self.highlighted=[]
    def show_current(self):
        self.clear_highlight()
        group=self.groups[self.index];fp=group[0];ref=fp.GetReference()
        self.render_token+=1
        self.progress.update(self.index+1,len(self.groups))
        self.footprint_preview.set_footprint(fp)
        names=preview.model_names(fp)
        cached=self.cached_models.get(ref)
        if cached:
            try:self.model_preview.set_image(cached)
            except Exception:self.model_preview.set_message('Image 3D indisponible.')
        elif names:
            self.model_preview.set_message('Chargement automatique du modèle 3D…')
            wx.CallAfter(self.show_model)
        else:self.model_preview.set_message('Aucun modèle 3D associé à cette empreinte.')
        refs=', '.join(item.GetReference() for item in group)
        self.heading.SetLabel('%s   ·   %d composants   ·   groupe %d/%d' % (fp.GetValue(),len(group),self.index+1,len(self.groups)))
        values={self.mapping[item.GetReference()] for item in group if item.GetReference() in self.mapping}
        self.visual.SetLabel('Empreintes concernées : '+refs+'\nUne seule référence sera appliquée à tout ce groupe. Exceptions modifiables à l’étape suivante.\nContrôle visuel : le premier composant est centré dans le PCB Editor si KiCad le permet.'+('\nAttention : ce groupe avait plusieurs attributions différentes ; vérifie-les dans la liste finale.' if len(values)>1 else ''))
        self.visual.Wrap(920)
        self.part.SetValue(next(iter(values)) if len(values)==1 else '')
        self.previous.Enable(self.index>0)
        self.next.label='Vérifier la liste  →' if self.index==len(self.groups)-1 else 'Suivant  →'
        self.next.Refresh()
        self.body.FitInside();self.Layout()
        try:
            import pcbnew
            for item in group:
                item.SetBrightened()
                self.highlighted.append(item)
            pcbnew.FocusOnItem(fp)
            pcbnew.Refresh()
        except Exception:
            self.visual.SetLabel(self.visual.GetLabel()+'\nSurbrillance KiCad indisponible : utilise les références affichées ci-dessus.')
    def save_current(self):
        value=self.part.GetValue().strip().upper()
        if PART.fullmatch(value):
            for fp in self.groups[self.index]:self.mapping[fp.GetReference()]=value
        else:
            wx.MessageBox('Saisis une référence LCSC (C suivi de chiffres), ou clique sur « Ne pas placer ».','Composant à vérifier',wx.OK|wx.ICON_WARNING)
            return False
        return True
    def forward(self,event):
        if not self.save_current(): return
        self.advance()
    def skip_group(self,event):
        for fp in self.groups[self.index]:self.mapping[fp.GetReference()]=''
        self.advance()
    def advance(self):
        if self.index==len(self.groups)-1:
            self.clear_highlight()
            self.EndModal(wx.ID_OK)
        else:
            self.index+=1;self.show_current()
    def back(self,event):
        if self.part.GetValue().strip() and not self.save_current():return
        self.index-=1;self.show_current()
    def on_close(self,event):
        self.clear_highlight()
        self.stop_preview()
        event.Skip()
    def stop_preview(self):
        self.closed=True
        self.pending_model=None
        if not self.rendering:self.preview_dir.cleanup()
    def show_model(self):
        if self.closed:return
        fp=self.groups[self.index][0];reference=fp.GetReference()
        cached=self.cached_models.get(reference)
        if cached:
            self.model_preview.set_image(cached)
            return
        if self.rendering:
            self.pending_model=reference
            return
        cli=preview.find_cli()
        if not cli:
            self.model_preview.set_message('kicad-cli est introuvable sur ce Mac.')
            return
        try:
            import pcbnew
            source=pcbnew.GetBoard().GetFileName()
            pcb=preview.prepare_single(source,reference,self.preview_dir.name)
        except Exception as exc:
            self.model_preview.set_message('3D indisponible : '+str(exc)[:45])
            return
        self.rendering=True
        self.model_preview.set_message('Rendu 3D en cours…')
        def work():
            try:result=(preview.render_prepared(pcb,cli),None)
            except Exception as exc:result=(None,str(exc))
            wx.CallAfter(done,result)
        def done(result):
            self.rendering=False
            if self.closed:
                self.preview_dir.cleanup()
                return
            image,error=result
            if image:self.cached_models[reference]=image
            current=self.groups[self.index][0].GetReference()
            if current==reference:
                if error:self.model_preview.set_message('3D indisponible : '+error[:45])
                else:
                    try:self.model_preview.set_image(image)
                    except Exception:self.model_preview.set_message('Impossible de lire le rendu 3D.')
            queued=self.pending_model
            self.pending_model=None
            if queued and queued==current and queued!=reference:self.show_model()
        threading.Thread(target=work,daemon=True).start()
    def open_site(self,event):
        fp=self.groups[self.index][0]
        webbrowser.open('https://jlcpcb.com/parts?searchTxt='+urllib.parse.quote(fp.GetValue()))
    def search(self,event):
        fp=self.groups[self.index][0]
        try:
            with wx.TextEntryDialog(self,'Référence fabricant, mot-clé ou numéro LCSC :','Recherche LCSC',fp.GetValue()) as dlg:
                if dlg.ShowModal()!=wx.ID_OK:return
                term=dlg.GetValue().strip()
            if not term:return
            found=search_lcsc(term)
            if not found:raise ValueError('Aucun résultat reconnu. Essaie une référence C… ou le catalogue JLCPCB.')
            options=['%s — %s — stock : %s' % item for item in found]
            with wx.SingleChoiceDialog(self,'Vérifie valeur et boîtier avant de choisir.','Résultats LCSC',options) as dlg:
                if dlg.ShowModal()==wx.ID_OK:
                    self.part.SetValue(found[dlg.GetSelection()][0])
        except Exception as exc:
            wx.MessageBox(str(exc),'Recherche LCSC',wx.OK|wx.ICON_ERROR)

class ReviewDialog(wx.Dialog):
    """Review every reference independently, including exceptions within a group."""
    def __init__(self,parent,groups,mapping,stock_notes=None):
        super().__init__(parent,title='Vérification des références avant export',size=(1100,600))
        self.SetBackgroundColour(preview.BG)
        self.mapping=dict(mapping)
        self.stock_notes=dict(stock_notes or {})
        self.fps=[fp for group in groups for fp in group]
        root=wx.BoxSizer(wx.VERTICAL)
        title=wx.StaticText(self,label='Vérifie tes composants')
        title.SetForegroundColour(preview.INK)
        font=title.GetFont();font.SetPointSize(font.GetPointSize()+5);font.SetWeight(wx.FONTWEIGHT_BOLD)
        title.SetFont(font)
        root.Add(title,0,wx.LEFT|wx.TOP,20)
        explanation=wx.StaticText(self,label='Double-clique pour corriger une pièce. Stock : relevé manuel daté, jamais une disponibilité en direct.')
        explanation.SetForegroundColour(preview.MUTED)
        root.Add(explanation,0,wx.ALL,20)
        self.rows=wx.ListCtrl(self,style=wx.LC_REPORT|wx.LC_SINGLE_SEL)
        self.rows.SetBackgroundColour(preview.CARD)
        self.rows.SetTextColour(preview.INK)
        for idx,(title,width) in enumerate((('Référence',90),('Valeur',150),('Empreinte',220),('Numéro LCSC / exclu',170),('Stock (source et date)',350))):
            self.rows.InsertColumn(idx,title,width=width)
        for fp in self.fps:
            row=self.rows.InsertItem(self.rows.GetItemCount(),fp.GetReference())
            self.rows.SetItem(row,1,fp.GetValue())
            self.rows.SetItem(row,2,str(fp.GetFPID().GetLibItemName()))
            self.rows.SetItem(row,3,self.mapping.get(fp.GetReference()) or 'Exclu')
            self.update_stock_cell(row)
        self.rows.Bind(wx.EVT_LIST_ITEM_ACTIVATED,self.edit)
        root.Add(self.rows,1,wx.EXPAND|wx.LEFT|wx.RIGHT,10)
        edit=preview.ActionButton(self,'Modifier la ligne',self.edit,width=170)
        root.Add(edit,0,wx.ALL,10)
        stock=preview.ActionButton(self,'Consulter le stock',self.edit_stock,width=200)
        root.Add(stock,0,wx.LEFT|wx.RIGHT|wx.BOTTOM,10)
        footer=wx.BoxSizer(wx.HORIZONTAL)
        footer.Add(preview.ActionButton(self,'Annuler',lambda event:self.EndModal(wx.ID_CANCEL),width=120),0,wx.RIGHT,12)
        footer.Add(preview.ActionButton(self,'Confirmer la liste  →',lambda event:self.EndModal(wx.ID_OK),'primary',width=220))
        root.Add(footer,0,wx.ALIGN_RIGHT|wx.ALL,20)
        self.SetSizer(root)
    def edit(self,event):
        idx=self.rows.GetFirstSelected()
        if idx<0:return
        ref=self.fps[idx].GetReference()
        with wx.TextEntryDialog(self,'Numéro C… ; laisser vide pour ne pas assembler '+ref,
                                'Corriger '+ref,self.mapping.get(ref,'')) as dlg:
            if dlg.ShowModal()!=wx.ID_OK:return
            value=dlg.GetValue().strip().upper()
        if value and not PART.fullmatch(value):
            wx.MessageBox('Le numéro doit être C suivi de chiffres, ou vide pour exclure.','Numéro invalide',wx.OK|wx.ICON_WARNING)
            return
        self.mapping[ref]=value
        self.rows.SetItem(idx,3,value or 'Exclu')
        self.update_stock_cell(idx)
    def update_stock_cell(self,idx):
        ref=self.fps[idx].GetReference()
        number=self.mapping.get(ref,'')
        note=self.stock_notes.get(number,{}) if number else {}
        if note and isinstance(note,dict):
            label='%s — %s, relevé %s' % (note.get('quantity','?'),note.get('source','?'),note.get('checked_at','?'))
        else:label='Non vérifié' if number else 'Sans assemblage'
        self.rows.SetItem(idx,4,label)
    def edit_stock(self,event):
        idx=self.rows.GetFirstSelected()
        if idx<0:return
        number=self.mapping.get(self.fps[idx].GetReference(),'')
        if not number:
            wx.MessageBox('Attribue d’abord un numéro de pièce.','Stock',wx.OK|wx.ICON_INFORMATION)
            return
        with wx.SingleChoiceDialog(self,'Ouvre le catalogue officiel et vérifie la quantité pour '+number+'.',
                                   'Source du relevé',['JLCPCB (stock assemblage)','LCSC (stock distributeur)']) as dlg:
            if dlg.ShowModal()!=wx.ID_OK:return
            source='JLCPCB' if dlg.GetSelection()==0 else 'LCSC'
        url=('https://jlcpcb.com/parts?searchTxt=' if source=='JLCPCB' else 'https://www.lcsc.com/search?q=')+urllib.parse.quote(number)
        webbrowser.open(url)
        with wx.TextEntryDialog(self,'Quantité affichée pour '+number+' sur '+source+' (nombre entier).\nLaisse vide pour supprimer le relevé.',
                                'Relever le stock',str(self.stock_notes.get(number,{}).get('quantity',''))) as dlg:
            if dlg.ShowModal()!=wx.ID_OK:return
            quantity=dlg.GetValue().strip()
        if quantity and (not quantity.isascii() or not quantity.isdecimal()):
            wx.MessageBox('Entre une quantité entière positive ou zéro.','Stock invalide',wx.OK|wx.ICON_WARNING)
            return
        if quantity:
            self.stock_notes[number]={'quantity':int(quantity),'source':source,
                                      'checked_at':datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %Z')}
        else:self.stock_notes.pop(number,None)
        for row in range(len(self.fps)):self.update_stock_cell(row)

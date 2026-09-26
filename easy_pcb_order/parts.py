"""Official LCSC partner search and user-reviewed part assignments."""
import hashlib
from datetime import datetime
import json
import os
from pathlib import Path
import re
import secrets
import time
import urllib.parse
import urllib.request
import webbrowser
import wx

PART = re.compile(r'^C[1-9][0-9]*$', re.I)

def part_key(fp):
    """Conservative grouping: same symbol family, value, footprint and assembly type."""
    ref=fp.GetReference()
    family=re.match(r'[A-Za-z]+',ref)
    family=family.group(0).upper() if family else ref.upper()
    value=' '.join(fp.GetValue().casefold().split())
    footprint=str(fp.GetFPID().GetLibItemName()).casefold()
    try:
        import pcbnew
        assembly='SMD' if fp.GetAttributes() & pcbnew.FP_SMD else 'THT'
    except (ImportError,AttributeError):
        assembly='unknown'
    details=[]
    if hasattr(fp,'GetFields'):
        for field in fp.GetFields():
            name=field.GetName().strip().casefold()
            if name in ('mpn','manufacturer','color','colour','tolerance','voltage','dielectric','power','wattage','current','intensity','polarity'):
                details.append((name,field.GetText().strip().casefold()))
    return family,value,footprint,assembly,tuple(sorted(details))

def group_footprints(footprints, assignments):
    """Keep conflicting known part numbers separate; never guess LED color or ratings."""
    groups={}
    for fp in footprints:
        key=part_key(fp)
        existing=assignments.get(fp.GetReference())
        if existing is not None:
            key+=(existing,)
        else:
            key+=(None,)
        groups.setdefault(key,[]).append(fp)
    return list(groups.values())

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

class PartsDialog(wx.Dialog):
    """One conservative group at a time, with canvas and dialog feedback."""
    def __init__(self,parent,footprints,mapping):
        super().__init__(parent,title='Étape 5 — composants à assembler',size=(760,440))
        self.mapping=dict(mapping)
        self.groups=group_footprints(footprints,self.mapping)
        self.index=0
        self.highlighted=[]
        root=wx.BoxSizer(wx.VERTICAL)
        self.heading=wx.StaticText(self,label='')
        root.Add(self.heading,0,wx.ALL,12)
        self.visual=wx.StaticText(self,label='')
        root.Add(self.visual,0,wx.LEFT|wx.RIGHT|wx.BOTTOM,12)
        self.part=wx.TextCtrl(self)
        row=wx.BoxSizer(wx.HORIZONTAL)
        row.Add(wx.StaticText(self,label='Référence LCSC :'),0,wx.ALIGN_CENTER_VERTICAL|wx.RIGHT,8)
        row.Add(self.part,1,wx.EXPAND)
        root.Add(row,0,wx.EXPAND|wx.LEFT|wx.RIGHT,12)
        self.omit=wx.CheckBox(self,label='Ne pas assembler ce composant')
        root.Add(self.omit,0,wx.ALL,12)
        actions=wx.BoxSizer(wx.HORIZONTAL)
        for title,handler in [('Rechercher LCSC',self.search),('Catalogue JLCPCB',self.open_site)]:
            b=wx.Button(self,label=title);b.Bind(wx.EVT_BUTTON,handler);actions.Add(b,0,wx.RIGHT,8)
        root.Add(actions,0,wx.LEFT|wx.RIGHT,12)
        root.Add(wx.StaticText(self,label='Même référence uniquement si valeur, boîtier, caractéristiques et polarité correspondent. Vérifie la fiche fabricant.'),0,wx.ALL,12)
        buttons=wx.BoxSizer(wx.HORIZONTAL)
        self.previous=wx.Button(self,label='Précédent')
        self.next=wx.Button(self,label='Suivant')
        cancel=wx.Button(self,wx.ID_CANCEL,label='Annuler')
        self.previous.Bind(wx.EVT_BUTTON,self.back)
        self.next.Bind(wx.EVT_BUTTON,self.forward)
        buttons.Add(self.previous,0,wx.RIGHT,8);buttons.Add(self.next,0,wx.RIGHT,8);buttons.Add(cancel)
        root.Add(buttons,0,wx.ALIGN_RIGHT|wx.ALL,12)
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
        refs=', '.join(item.GetReference() for item in group)
        self.heading.SetLabel('Groupe %d / %d — %d composant(s)  •  %s  •  %s' % (self.index+1,len(self.groups),len(group),fp.GetValue(),fp.GetFPID().GetLibItemName()))
        self.visual.SetLabel('Empreintes concernées : '+refs+'\nType : '+part_key(fp)[0]+'  •  Ces empreintes recevront le même numéro.\nContrôle visuel : le premier composant du groupe est centré dans le PCB Editor si KiCad le permet.')
        self.part.SetValue(self.mapping.get(ref,''))
        self.omit.SetValue(ref in self.mapping and not self.mapping[ref])
        self.previous.Enable(self.index>0)
        self.next.SetLabel('Vérifier la liste' if self.index==len(self.groups)-1 else 'Suivant')
        self.Layout()
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
        if self.omit.GetValue():
            for fp in self.groups[self.index]:self.mapping[fp.GetReference()]=''
        elif PART.fullmatch(value):
            for fp in self.groups[self.index]:self.mapping[fp.GetReference()]=value
        else:
            wx.MessageBox('Saisis une référence LCSC (C suivi de chiffres), ou coche « Ne pas assembler ».','Composant à vérifier',wx.OK|wx.ICON_WARNING)
            return False
        return True
    def forward(self,event):
        if not self.save_current(): return
        if self.index==len(self.groups)-1:
            self.clear_highlight()
            self.EndModal(wx.ID_OK)
        else:
            self.index+=1;self.show_current()
    def back(self,event):
        if not self.save_current(): return
        self.index-=1;self.show_current()
    def on_close(self,event):
        self.clear_highlight()
        event.Skip()
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
                    self.part.SetValue(found[dlg.GetSelection()][0]);self.omit.SetValue(False)
        except Exception as exc:
            wx.MessageBox(str(exc),'Recherche LCSC',wx.OK|wx.ICON_ERROR)

class ReviewDialog(wx.Dialog):
    """Review every reference independently, including exceptions within a group."""
    def __init__(self,parent,groups,mapping,stock_notes=None):
        super().__init__(parent,title='Vérification des références avant export',size=(1100,600))
        self.mapping=dict(mapping)
        self.stock_notes=dict(stock_notes or {})
        self.fps=[fp for group in groups for fp in group]
        root=wx.BoxSizer(wx.VERTICAL)
        root.Add(wx.StaticText(self,label='Double-clique pour corriger une pièce. Stock : relevé manuel daté, jamais une disponibilité en direct.'),0,wx.ALL,10)
        self.rows=wx.ListCtrl(self,style=wx.LC_REPORT|wx.LC_SINGLE_SEL)
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
        edit=wx.Button(self,label='Modifier la ligne sélectionnée')
        edit.Bind(wx.EVT_BUTTON,self.edit)
        root.Add(edit,0,wx.ALL,10)
        stock=wx.Button(self,label='Consulter / renseigner le stock de la pièce sélectionnée')
        stock.Bind(wx.EVT_BUTTON,self.edit_stock)
        root.Add(stock,0,wx.LEFT|wx.RIGHT|wx.BOTTOM,10)
        root.Add(self.CreateButtonSizer(wx.OK|wx.CANCEL),0,wx.EXPAND|wx.ALL,10)
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

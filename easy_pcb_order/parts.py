"""Official LCSC partner search and user-reviewed part assignments."""
import hashlib
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

class PartsDialog(wx.Dialog):
    def __init__(self,parent,footprints,mapping):
        super().__init__(parent,title='Choisir les composants avant la BOM',size=(780,600))
        self.mapping=dict(mapping)
        self.fps=footprints
        root=wx.BoxSizer(wx.VERTICAL)
        root.Add(wx.StaticText(self,label='Sélectionne une empreinte, recherche sa pièce, puis confirme le numéro LCSC. Les choix seront enregistrés dans un fichier du projet.'),0,wx.ALL,9)
        self.list=wx.ListCtrl(self,style=wx.LC_REPORT|wx.LC_SINGLE_SEL)
        for col,width in [('Référence',85),('Valeur',150),('Empreinte',210),('Référence LCSC',125),('Mode',100)]:
            self.list.InsertColumn(self.list.GetColumnCount(),col,width=width)
        for i,fp in enumerate(footprints):
            ref=fp.GetReference()
            self.list.InsertItem(i,ref)
            self.list.SetItem(i,1,fp.GetValue())
            self.list.SetItem(i,2,str(fp.GetFPID().GetLibItemName()))
            self.list.SetItem(i,3,self.mapping.get(ref,''))
            self.list.SetItem(i,4,'Assemblé' if self.mapping.get(ref) else 'Non assemblé')
        root.Add(self.list,1,wx.EXPAND|wx.ALL,8)
        actions=wx.BoxSizer(wx.HORIZONTAL)
        for title,action in [('Recherche LCSC API',self.search),('Chercher sur JLCPCB',self.open_site),('Saisir C…',self.set_number),('Ne pas assembler',self.skip)]:
            button=wx.Button(self,label=title); button.Bind(wx.EVT_BUTTON,action); actions.Add(button,0,wx.RIGHT,6)
        root.Add(actions,0,wx.ALL,8)
        root.Add(wx.StaticText(self,label='L’API LCSC exige LCSC_API_KEY et LCSC_API_SECRET. Vérifie empreinte, tension, tolérance, stock et compatibilité JLCPCB avant de choisir.'),0,wx.ALL,9)
        root.Add(self.CreateButtonSizer(wx.OK|wx.CANCEL),0,wx.EXPAND|wx.ALL,9)
        self.SetSizer(root)
    def selected(self):
        i=self.list.GetFirstSelected()
        if i<0:
            raise ValueError('Sélectionne une ligne d’abord.')
        return i,self.fps[i]
    def assign(self,num):
        i,fp=self.selected()
        if num and not PART.fullmatch(num):
            raise ValueError('La référence doit être au format C suivi de chiffres (exemple : C25804).')
        self.mapping[fp.GetReference()]=num.upper()
        self.list.SetItem(i,3,num.upper())
        self.list.SetItem(i,4,'Assemblé' if num else 'Non assemblé')
    def set_number(self,event):
        try:
            i,fp=self.selected()
            with wx.TextEntryDialog(self,'Numéro choisi dans le catalogue JLCPCB/LCSC :',fp.GetReference(),self.mapping.get(fp.GetReference(),'')) as dlg:
                if dlg.ShowModal()==wx.ID_OK:
                    self.assign(dlg.GetValue().strip())
        except Exception as e:
            wx.MessageBox(str(e),'Composants',wx.OK|wx.ICON_ERROR)
    def skip(self,event):
        try: self.assign('')
        except Exception as e: wx.MessageBox(str(e),'Composants',wx.OK|wx.ICON_ERROR)
    def open_site(self,event):
        try:
            _,fp=self.selected()
            term=urllib.parse.quote(fp.GetValue())
            webbrowser.open('https://jlcpcb.com/parts?searchTxt='+term)
        except Exception as e: wx.MessageBox(str(e),'Composants',wx.OK|wx.ICON_ERROR)
    def search(self,event):
        try:
            _,fp=self.selected()
            with wx.TextEntryDialog(self,'Référence fabricant, mot-clé ou numéro LCSC :','Recherche LCSC',fp.GetValue()) as dlg:
                if dlg.ShowModal()!=wx.ID_OK: return
                term=dlg.GetValue().strip()
            if not term: return
            found=search_lcsc(term)
            if not found: raise ValueError('Aucun résultat reconnu ; essaie le numéro C… ou recherche sur le site.')
            options=['%s  —  %s  —  stock : %s' % r for r in found]
            with wx.SingleChoiceDialog(self,'Vérifie la fiche fabricant et l’empreinte avant d’affecter.','Résultats LCSC',options) as dlg:
                if dlg.ShowModal()==wx.ID_OK:
                    self.assign(found[dlg.GetSelection()][0])
        except Exception as e:
            wx.MessageBox(str(e),'Recherche LCSC',wx.OK|wx.ICON_ERROR)

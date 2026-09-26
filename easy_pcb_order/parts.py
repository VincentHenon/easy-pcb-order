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
    """One footprint at a time, with a temporary KiCad canvas highlight."""
    def __init__(self,parent,footprints,mapping):
        super().__init__(parent,title='Étape 5 — composants à assembler',size=(620,320))
        self.mapping=dict(mapping)
        self.fps=footprints
        self.index=0
        self.highlighted=None
        root=wx.BoxSizer(wx.VERTICAL)
        self.heading=wx.StaticText(self,label='')
        root.Add(self.heading,0,wx.ALL,12)
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
        root.Add(wx.StaticText(self,label='Vérifie la valeur et l’empreinte dans la fiche du fabricant avant de confirmer.'),0,wx.ALL,12)
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
        if self.highlighted is not None:
            try:
                self.highlighted.ClearBrightened()
                import pcbnew
                pcbnew.Refresh()
            except Exception:
                pass
            self.highlighted=None
    def show_current(self):
        self.clear_highlight()
        fp=self.fps[self.index]
        ref=fp.GetReference()
        self.heading.SetLabel('%d / %d — %s  •  %s  •  %s' % (self.index+1,len(self.fps),ref,fp.GetValue(),fp.GetFPID().GetLibItemName()))
        self.part.SetValue(self.mapping.get(ref,''))
        self.omit.SetValue(ref in self.mapping and not self.mapping[ref])
        self.previous.Enable(self.index>0)
        self.next.SetLabel('Terminer' if self.index==len(self.fps)-1 else 'Suivant')
        self.Layout()
        try:
            import pcbnew
            fp.SetBrightened()
            self.highlighted=fp
            pcbnew.FocusOnItem(fp)
            pcbnew.Refresh()
        except Exception:
            # Continue the assignment process if this KiCad build cannot focus the canvas.
            self.highlighted=None
    def save_current(self):
        fp=self.fps[self.index]
        value=self.part.GetValue().strip().upper()
        if self.omit.GetValue():
            self.mapping[fp.GetReference()]=''
        elif PART.fullmatch(value):
            self.mapping[fp.GetReference()]=value
        else:
            wx.MessageBox('Saisis une référence LCSC (C suivi de chiffres), ou coche « Ne pas assembler ».','Composant à vérifier',wx.OK|wx.ICON_WARNING)
            return False
        return True
    def forward(self,event):
        if not self.save_current(): return
        if self.index==len(self.fps)-1:
            self.clear_highlight();self.EndModal(wx.ID_OK)
        else:
            self.index+=1;self.show_current()
    def back(self,event):
        if not self.save_current(): return
        self.index-=1;self.show_current()
    def on_close(self,event):
        self.clear_highlight()
        event.Skip()
    def open_site(self,event):
        fp=self.fps[self.index]
        webbrowser.open('https://jlcpcb.com/parts?searchTxt='+urllib.parse.quote(fp.GetValue()))
    def search(self,event):
        fp=self.fps[self.index]
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

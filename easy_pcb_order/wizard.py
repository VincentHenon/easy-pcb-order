"""Small, explicit fabrication workflow dialogs for the KiCad PCB editor."""
import wx
import pcbnew
from . import geometry

FACTORIES=['JLCPCB','PCBWay','Autre fabricant (Gerber + BOM + placement)']

def project_mode():
    with wx.SingleChoiceDialog(None,'Combien de designs PCB distincts contient ce fichier ?',
                               'Étape 1 / 6 — Projet',['Un seul design','Plusieurs designs']) as dlg:
        if dlg.ShowModal()!=wx.ID_OK:return None
        return 1 if dlg.GetSelection()==0 else 2

def verify_detection(contours,board,mode):
    count=len(contours)
    if (mode==1 and count!=1) or (mode==2 and count<2):
        wx.MessageBox('Tu as indiqué %s, mais %d contour(s) extérieur(s) ont été détecté(s). Vérifie les Edge.Cuts avant de relancer.' %
                      ('un seul design' if mode==1 else 'plusieurs designs',count),
                      'Étape 2 / 6 — détection incohérente',wx.OK|wx.ICON_WARNING)
        return False
    lines=[]
    for i,c in enumerate(contours):
        xs=[p[0] for p in c['poly']];ys=[p[1] for p in c['poly']]
        refs=[fp.GetReference() for fp in board.GetFootprints()
              if geometry.contains(((fp.GetBoundingBox().GetLeft()+fp.GetBoundingBox().GetRight())/2,
                                    (fp.GetBoundingBox().GetTop()+fp.GetBoundingBox().GetBottom())/2),c['poly'])]
        lines.append('%d. %.1f × %.1f mm • %d découpe(s) • %d empreinte(s) : %s' %
                     (i+1,pcbnew.ToMM(max(xs)-min(xs)),pcbnew.ToMM(max(ys)-min(ys)),
                      len(c['holes']),len(refs),', '.join(refs[:6]) or 'aucune'))
    message='%d design(s) détecté(s) (ordre de gauche à droite) :\n\n%s\n\nTous tes designs sont-ils présents ?' % (count,'\n'.join(lines))
    with wx.MessageDialog(None,message,'Étape 2 / 6 — vérifier les designs',wx.YES_NO|wx.ICON_QUESTION) as dlg:
        return dlg.ShowModal()==wx.ID_YES

class LayerDialog(wx.Dialog):
    def __init__(self,contours,source_count):
        super().__init__(None,title='Étape 3 / 6 — couches de chaque design',size=(590,330))
        root=wx.BoxSizer(wx.VERTICAL)
        root.Add(wx.StaticText(self,label='Sélectionne les couches de chaque PCB. La pile source a %d couches.' % source_count),0,wx.ALL,12)
        grid=wx.FlexGridSizer(len(contours)+1,3,9,12)
        for h in ('Design','Couches cuivre','Front panel (sans assemblage)'):
            grid.Add(wx.StaticText(self,label=h))
        self.rows=[]
        for i,c in enumerate(contours):
            xs=[p[0] for p in c['poly']];ys=[p[1] for p in c['poly']]
            grid.Add(wx.StaticText(self,label='%d — %.1f × %.1f mm' %
                    (i+1,pcbnew.ToMM(max(xs)-min(xs)),pcbnew.ToMM(max(ys)-min(ys)))))
            choice=wx.Choice(self,choices=[str(n) for n in range(2,source_count+1,2)])
            choice.SetStringSelection(str(source_count));grid.Add(choice)
            panel=wx.CheckBox(self);grid.Add(panel)
            self.rows.append((choice,panel))
        root.Add(grid,0,wx.ALL,12)
        root.Add(wx.StaticText(self,label='Les couches internes utilisées par un design ne peuvent pas être supprimées. Le plugin vérifie cela avant export.'),0,wx.ALL,12)
        root.Add(self.CreateButtonSizer(wx.OK|wx.CANCEL),0,wx.EXPAND|wx.ALL,10)
        self.SetSizer(root)
    def values(self):
        return [(int(choice.GetStringSelection()),panel.GetValue()) for choice,panel in self.rows]

class NameDialog(wx.Dialog):
    def __init__(self,contours):
        super().__init__(None,title='Étape 4 / 6 — nommer les designs',size=(530,290))
        root=wx.BoxSizer(wx.VERTICAL)
        root.Add(wx.StaticText(self,label='Donne un nom distinct à chaque design :'),0,wx.ALL,12)
        self.names=[]
        for i in range(len(contours)):
            row=wx.BoxSizer(wx.HORIZONTAL)
            row.Add(wx.StaticText(self,label='Design %d :' % (i+1)),0,wx.ALIGN_CENTER_VERTICAL|wx.RIGHT,12)
            ctrl=wx.TextCtrl(self,value='PCB_%d' % (i+1))
            row.Add(ctrl,1,wx.EXPAND)
            root.Add(row,0,wx.EXPAND|wx.LEFT|wx.RIGHT|wx.BOTTOM,12)
            self.names.append(ctrl)
        root.Add(self.CreateButtonSizer(wx.OK|wx.CANCEL),0,wx.EXPAND|wx.ALL,10)
        self.SetSizer(root)
    def values(self):
        return [ctrl.GetValue().strip() for ctrl in self.names]

def factory_choice():
    with wx.SingleChoiceDialog(None,'Choisis le fabricant et le format des fichiers :',
                               'Étape 6 / 6 — usine',FACTORIES) as dlg:
        if dlg.ShowModal()!=wx.ID_OK:return None
        return FACTORIES[dlg.GetSelection()]

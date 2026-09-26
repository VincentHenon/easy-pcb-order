"""Visual, guided fabrication workflow for KiCad PCB Editor."""
import wx
import pcbnew
from . import geometry

FACTORIES=['JLCPCB','PCBWay','Autre fabricant (Gerber + BOM + placement)']

def components_for(board,contour):
    result=[]
    for fp in board.GetFootprints():
        box=fp.GetBoundingBox()
        point=((box.GetLeft()+box.GetRight())/2,(box.GetTop()+box.GetBottom())/2)
        if geometry.contains(point,contour['poly']) and not any(geometry.contains(point,h) for h in contour['holes']):
            result.append(fp)
    return result

class DesignPreview(wx.Panel):
    """Simple 2D preview from KiCad geometry; no temporary Gerber required."""
    def __init__(self,parent,contour,board,width=330,height=220):
        super().__init__(parent,size=(width,height))
        self.contour=contour
        self.footprints=components_for(board,contour)
        self.tracks=[]
        for track in board.GetTracks():
            if isinstance(track,pcbnew.PCB_VIA):continue
            try:
                if geometry.owner((track.GetStart().x,track.GetStart().y),[contour])==0:
                    self.tracks.append(track)
            except ValueError:pass
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.Bind(wx.EVT_PAINT,self.paint)
    def paint(self,event):
        dc=wx.AutoBufferedPaintDC(self)
        dc.SetBackground(wx.Brush(wx.Colour(247,248,250)))
        dc.Clear()
        outer=self.contour['poly']
        xs=[p[0] for p in outer];ys=[p[1] for p in outer]
        left,right=min(xs),max(xs);top,bottom=min(ys),max(ys)
        w,h=self.GetClientSize()
        scale=min((w-36)/max(1,right-left),(h-36)/max(1,bottom-top))
        ox=(w-(right-left)*scale)/2;oy=(h-(bottom-top)*scale)/2
        def point(p):return wx.Point(int(ox+(p[0]-left)*scale),int(oy+(p[1]-top)*scale))
        dc.SetPen(wx.Pen(wx.Colour(42,83,132),2))
        dc.SetBrush(wx.Brush(wx.Colour(214,232,245)))
        dc.DrawPolygon([point(p) for p in outer])
        dc.SetPen(wx.Pen(wx.Colour(175,73,73),2))
        dc.SetBrush(wx.Brush(wx.Colour(247,248,250)))
        for hole in self.contour['holes']:
            dc.DrawPolygon([point(p) for p in hole])
        dc.SetPen(wx.Pen(wx.Colour(51,151,106),1))
        for track in self.tracks:
            a=point((track.GetStart().x,track.GetStart().y))
            b=point((track.GetEnd().x,track.GetEnd().y))
            dc.DrawLine(a.x,a.y,b.x,b.y)
        dc.SetPen(wx.Pen(wx.Colour(127,92,20),1))
        dc.SetBrush(wx.Brush(wx.Colour(251,197,80)))
        for fp in self.footprints:
            p=point((fp.GetPosition().x,fp.GetPosition().y))
            dc.DrawCircle(p.x,p.y,3)
            dc.DrawText(fp.GetReference(),p.x+5,p.y-8)

def card_caption(index,contour,board):
    xs=[p[0] for p in contour['poly']];ys=[p[1] for p in contour['poly']]
    refs=[fp.GetReference() for fp in components_for(board,contour)]
    return 'Design %d • %.1f × %.1f mm • %d découpe(s) • %d empreinte(s)\n%s' % (
        index+1,pcbnew.ToMM(max(xs)-min(xs)),pcbnew.ToMM(max(ys)-min(ys)),
        len(contour['holes']),len(refs),', '.join(refs[:10]) or 'Aucune empreinte')

def preview_card(parent,index,contour,board,controls=None):
    box=wx.BoxSizer(wx.VERTICAL)
    box.Add(wx.StaticText(parent,label=card_caption(index,contour,board)),0,wx.BOTTOM,5)
    box.Add(DesignPreview(parent,contour,board),0,wx.BOTTOM,6)
    if controls:box.Add(controls,0,wx.EXPAND)
    return box

def project_mode():
    with wx.SingleChoiceDialog(None,'Combien de designs PCB distincts contient ce fichier ?',
                               'Étape 1 / 6 — Projet',['Un seul design','Plusieurs designs']) as dlg:
        if dlg.ShowModal()!=wx.ID_OK:return None
        return 1 if dlg.GetSelection()==0 else 2

class DetectionDialog(wx.Dialog):
    def __init__(self,contours,board):
        super().__init__(None,title='Étape 2 / 6 — aperçu des designs détectés',size=(790,640))
        root=wx.BoxSizer(wx.VERTICAL)
        root.Add(wx.StaticText(self,label='Vérifie les contours, trous, pistes et composants. Chaque vignette représente un fichier à fabriquer.'),0,wx.ALL,10)
        scroll=wx.ScrolledWindow(self,style=wx.VSCROLL)
        grid=wx.GridSizer(0,2,12,12)
        self.confirmations=[]
        for i,c in enumerate(contours):
            card=wx.Panel(scroll)
            check=wx.CheckBox(card,label='Ce design est bien détecté')
            card.SetSizer(preview_card(card,i,c,board,check))
            grid.Add(card,0,wx.EXPAND|wx.ALL,5)
            self.confirmations.append(check)
        scroll.SetSizer(grid);scroll.SetScrollRate(0,15)
        root.Add(scroll,1,wx.EXPAND|wx.ALL,10)
        root.Add(self.CreateButtonSizer(wx.OK|wx.CANCEL),0,wx.EXPAND|wx.ALL,10)
        self.SetSizer(root)
    def all_confirmed(self):
        return all(check.GetValue() for check in self.confirmations)

def verify_detection(contours,board,mode):
    count=len(contours)
    if (mode==1 and count!=1) or (mode==2 and count<2):
        wx.MessageBox('Tu as indiqué %s, mais %d contour(s) extérieur(s) ont été détecté(s). Vérifie les Edge.Cuts avant de relancer.' %
                      ('un seul design' if mode==1 else 'plusieurs designs',count),
                      'Étape 2 / 6 — détection incohérente',wx.OK|wx.ICON_WARNING)
        return False
    with DetectionDialog(contours,board) as dlg:
        if dlg.ShowModal()!=wx.ID_OK:return False
        if dlg.all_confirmed():return True
    wx.MessageBox('Confirme chaque vignette avant de continuer.','Étape 2 / 6',wx.OK|wx.ICON_WARNING)
    return False

class LayerDialog(wx.Dialog):
    def __init__(self,contours,board):
        source_count=board.GetCopperLayerCount()
        super().__init__(None,title='Étape 3 / 6 — couches de chaque design',size=(790,640))
        root=wx.BoxSizer(wx.VERTICAL)
        root.Add(wx.StaticText(self,label='Pile source : %d couches. Choisis les couches de chaque carte à côté de son aperçu.' % source_count),0,wx.ALL,10)
        scroll=wx.ScrolledWindow(self,style=wx.VSCROLL)
        grid=wx.GridSizer(0,2,12,12)
        self.rows=[]
        for i,c in enumerate(contours):
            card=wx.Panel(scroll)
            controls=wx.BoxSizer(wx.VERTICAL)
            row=wx.BoxSizer(wx.HORIZONTAL)
            row.Add(wx.StaticText(card,label='Couches cuivre :'),0,wx.ALIGN_CENTER_VERTICAL|wx.RIGHT,8)
            choice=wx.Choice(card,choices=[str(n) for n in range(2,source_count+1,2)])
            choice.SetStringSelection(str(source_count));row.Add(choice)
            controls.Add(row,0,wx.BOTTOM,6)
            panel=wx.CheckBox(card,label='Front panel (pas d’assemblage)')
            controls.Add(panel)
            card.SetSizer(preview_card(card,i,c,board,controls))
            grid.Add(card,0,wx.EXPAND|wx.ALL,5)
            self.rows.append((choice,panel))
        scroll.SetSizer(grid);scroll.SetScrollRate(0,15)
        root.Add(scroll,1,wx.EXPAND|wx.ALL,10)
        root.Add(self.CreateButtonSizer(wx.OK|wx.CANCEL),0,wx.EXPAND|wx.ALL,10)
        self.SetSizer(root)
    def values(self):return [(int(choice.GetStringSelection()),panel.GetValue()) for choice,panel in self.rows]

class NameDialog(wx.Dialog):
    def __init__(self,contours,board):
        super().__init__(None,title='Étape 4 / 6 — nommer les designs',size=(790,640))
        root=wx.BoxSizer(wx.VERTICAL)
        scroll=wx.ScrolledWindow(self,style=wx.VSCROLL)
        grid=wx.GridSizer(0,2,12,12)
        self.names=[]
        for i,c in enumerate(contours):
            card=wx.Panel(scroll)
            field=wx.TextCtrl(card,value='PCB_%d' % (i+1))
            card.SetSizer(preview_card(card,i,c,board,field))
            grid.Add(card,0,wx.EXPAND|wx.ALL,5)
            self.names.append(field)
        scroll.SetSizer(grid);scroll.SetScrollRate(0,15)
        root.Add(scroll,1,wx.EXPAND|wx.ALL,10)
        root.Add(self.CreateButtonSizer(wx.OK|wx.CANCEL),0,wx.EXPAND|wx.ALL,10)
        self.SetSizer(root)
    def values(self):return [ctrl.GetValue().strip() for ctrl in self.names]

def factory_choice():
    with wx.SingleChoiceDialog(None,'Choisis le fabricant et le format des fichiers :',
                               'Étape 6 / 6 — usine',FACTORIES) as dlg:
        if dlg.ShowModal()!=wx.ID_OK:return None
        return FACTORIES[dlg.GetSelection()]

"""Footprint preview: local 2D drawing and optional KiCad CLI 3D render."""
from pathlib import Path
import shutil
import subprocess
import wx

BG=wx.Colour(23,29,42)
CARD=wx.Colour(34,43,59)
INK=wx.Colour(239,244,252)
MUTED=wx.Colour(165,181,202)
ACCENT=wx.Colour(102,195,226)
FIELD=wx.Colour(44,55,73)

class ActionButton(wx.Control):
    """Drawn control with keyboard access and visible hover/focus states."""
    def __init__(self,parent,label,callback,variant='secondary',width=150):
        super().__init__(parent,size=(width,40),style=wx.BORDER_NONE|wx.WANTS_CHARS)
        self.label,self.callback,self.variant=label,callback,variant
        self.SetName(label)
        self.hover=False
        self.SetMinSize((width,40));self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.Bind(wx.EVT_PAINT,self.paint)
        self.Bind(wx.EVT_LEFT_UP,self.activate)
        self.Bind(wx.EVT_ENTER_WINDOW,self.enter)
        self.Bind(wx.EVT_LEAVE_WINDOW,self.leave)
        self.Bind(wx.EVT_KEY_DOWN,self.key)
        self.Bind(wx.EVT_SET_FOCUS,lambda event:self.Refresh())
        self.Bind(wx.EVT_KILL_FOCUS,lambda event:self.Refresh())
    def enter(self,event):self.hover=True;self.Refresh()
    def leave(self,event):self.hover=False;self.Refresh()
    def key(self,event):
        if event.GetKeyCode() in (wx.WXK_RETURN,wx.WXK_NUMPAD_ENTER,wx.WXK_SPACE):self.activate(event)
        else:event.Skip()
    def activate(self,event):
        if not self.IsEnabled():return
        self.SetFocus()
        self.callback(event)
    def paint(self,event):
        dc=wx.AutoBufferedPaintDC(self);dc.SetBackground(wx.Brush(BG));dc.Clear()
        w,h=self.GetClientSize()
        fill=ACCENT if self.variant=='primary' else FIELD
        if self.hover:fill=wx.Colour(130,216,241) if self.variant=='primary' else wx.Colour(60,75,95)
        dc.SetPen(wx.Pen(ACCENT if self.HasFocus() else fill,2 if self.HasFocus() else 1))
        dc.SetBrush(wx.Brush(fill));dc.DrawRoundedRectangle(2,2,w-4,h-4,9)
        dc.SetTextForeground(BG if self.variant=='primary' else INK)
        tw,th=dc.GetTextExtent(self.label)
        dc.DrawText(self.label,(w-tw)//2,(h-th)//2)

class ProgressTrack(wx.Panel):
    def __init__(self,parent):
        super().__init__(parent,size=(-1,5))
        self.step=0;self.total=1
        self.SetMinSize((-1,5));self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.Bind(wx.EVT_PAINT,self.paint)
    def update(self,step,total):self.step=step;self.total=max(total,1);self.Refresh()
    def paint(self,event):
        dc=wx.AutoBufferedPaintDC(self);dc.SetBackground(wx.Brush(BG));dc.Clear()
        w,h=self.GetClientSize()
        dc.SetPen(wx.TRANSPARENT_PEN);dc.SetBrush(wx.Brush(FIELD));dc.DrawRoundedRectangle(0,0,w,h,2)
        dc.SetBrush(wx.Brush(ACCENT));dc.DrawRoundedRectangle(0,0,max(4,int(w*self.step/self.total)),h,2)

class FootprintPreview(wx.Panel):
    def __init__(self,parent):
        super().__init__(parent,size=(400,255))
        self.fp=None
        self.SetMinSize((400,255))
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.Bind(wx.EVT_PAINT,self.paint)
    def set_footprint(self,fp):
        self.fp=fp
        self.Refresh()
    def paint(self,event):
        dc=wx.AutoBufferedPaintDC(self)
        dc.SetBackground(wx.Brush(CARD));dc.Clear()
        w,h=self.GetClientSize()
        dc.SetTextForeground(MUTED);dc.DrawText('EMPREINTE  ·  VUE 2D',16,13)
        if not self.fp:return
        fp=self.fp
        try:
            pads=list(fp.Pads())
            graphics=list(fp.GraphicalItems())
            try:box=fp.GetBoundingBox(False,False)
            except TypeError:box=fp.GetBoundingBox()
            if box is None:return
            left,right=box.GetLeft(),box.GetRight();top,bottom=box.GetTop(),box.GetBottom()
            if right<=left or bottom<=top:return
            scale=min((w-65)/(right-left),(h-80)/(bottom-top))
            ox=(w-(right-left)*scale)/2;oy=48+(h-73-(bottom-top)*scale)/2
            def xy(x,y):return int(ox+(x-left)*scale),int(oy+(y-top)*scale)
            dc.SetBrush(wx.TRANSPARENT_BRUSH)
            import pcbnew
            silk=pcbnew.B_SilkS if fp.IsFlipped() else pcbnew.F_SilkS
            fab=pcbnew.B_Fab if fp.IsFlipped() else pcbnew.F_Fab
            for graphic in graphics:
                try:
                    layer=graphic.GetLayer()
                    if layer not in (silk,fab):continue
                    dc.SetPen(wx.Pen(INK if layer==silk else MUTED,2 if layer==silk else 1))
                    if hasattr(graphic,'GetText'):
                        label=graphic.GetText()
                        if label and not label.startswith('${'):
                            p=graphic.GetPosition();x,y=xy(p.x,p.y)
                            dc.SetTextForeground(INK if layer==silk else MUTED)
                            dc.DrawText(label[:20],x,y)
                        continue
                    kind=graphic.GetShapeStr().lower()
                    if 'circle' in kind:
                        center=graphic.GetCenter();cx,cy=xy(center.x,center.y)
                        radius=max(1,int(graphic.GetRadius()*scale))
                        dc.DrawCircle(cx,cy,radius)
                    elif 'rect' in kind:
                        b=graphic.GetBoundingBox();x1,y1=xy(b.GetLeft(),b.GetTop());x2,y2=xy(b.GetRight(),b.GetBottom())
                        dc.DrawRectangle(x1,y1,max(1,x2-x1),max(1,y2-y1))
                    elif 'arc' in kind:
                        a=graphic.GetStart();m=graphic.GetArcMid();z=graphic.GetEnd()
                        x1,y1=xy(a.x,a.y);xm,ym=xy(m.x,m.y);x2,y2=xy(z.x,z.y)
                        dc.DrawSpline([wx.Point(x1,y1),wx.Point(xm,ym),wx.Point(x2,y2)])
                    else:
                        start,end=graphic.GetStart(),graphic.GetEnd()
                        x1,y1=xy(start.x,start.y);x2,y2=xy(end.x,end.y)
                        dc.DrawLine(x1,y1,x2,y2)
                except (AttributeError,TypeError,ValueError):continue
            dc.SetPen(wx.Pen(wx.Colour(250,203,122),1))
            dc.SetBrush(wx.Brush(wx.Colour(208,153,79)))
            for pad in pads:
                b=pad.GetBoundingBox()
                x1,y1=xy(b.GetLeft(),b.GetTop());x2,y2=xy(b.GetRight(),b.GetBottom())
                dc.DrawRoundedRectangle(x1,y1,max(3,x2-x1),max(3,y2-y1),3)
                dc.SetTextForeground(BG)
                number=str(pad.GetNumber())
                if number and x2-x1>16 and y2-y1>12:dc.DrawText(number,x1+3,y1+1)
        except Exception:
            dc.SetTextForeground(MUTED);dc.DrawText('Aperçu 2D indisponible pour cette empreinte.',16,80)

def model_names(fp):
    try:return [str(model.m_Filename) for model in fp.Models() if model.m_Filename]
    except (AttributeError,TypeError):return []

def find_cli():
    cli=shutil.which('kicad-cli')
    if cli:return cli
    for path in ('/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli','/Applications/KiCad/kicad-cli'):
        if Path(path).is_file():return path
    return None

def prepare_single(source,reference,directory):
    """Create a disposable board with only this footprint on KiCad's UI thread."""
    import pcbnew
    board=pcbnew.LoadBoard(str(source))
    matches=[fp for fp in board.GetFootprints() if fp.GetReference()==reference]
    if len(matches)!=1:raise ValueError('Empreinte introuvable ou non unique : '+reference)
    fp=matches[0]
    if not model_names(fp):raise ValueError('Aucun modèle 3D associé à '+reference+' dans KiCad.')
    for collection in (board.GetFootprints(),board.GetTracks(),board.Zones(),board.GetDrawings()):
        for item in list(collection):
            if item is not fp:board.Remove(item)
    b=fp.GetBoundingBox(False,False)
    margin=pcbnew.FromMM(1.5)
    shape=pcbnew.PCB_SHAPE(board)
    shape.SetShape(pcbnew.SHAPE_T_RECT)
    shape.SetStart(pcbnew.VECTOR2I(b.GetLeft()-margin,b.GetTop()-margin))
    shape.SetEnd(pcbnew.VECTOR2I(b.GetRight()+margin,b.GetBottom()+margin))
    shape.SetLayer(pcbnew.Edge_Cuts)
    board.Add(shape)
    pcb=Path(directory)/(reference+'.kicad_pcb')
    if not pcbnew.SaveBoard(str(pcb),board):raise RuntimeError('Impossible de préparer le modèle 3D.')
    return pcb

def render_prepared(pcb,cli):
    png=pcb.with_suffix('.png')
    result=subprocess.run([cli,'pcb','render','--width','640','--height','400',
                           '--quality','basic','--side','top','--rotate=-30,0,30',
                           '--output',str(png),str(pcb)],capture_output=True,text=True,timeout=60)
    if result.returncode or not png.is_file():
        raise RuntimeError((result.stderr or result.stdout or 'Rendu 3D impossible.').strip()[-500:])
    return png

class ModelPreview(wx.Panel):
    def __init__(self,parent):
        super().__init__(parent,size=(400,255))
        self.bitmap=None
        self.message='Le modèle 3D se charge automatiquement quand il est disponible.'
        self.SetMinSize((400,255));self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.Bind(wx.EVT_PAINT,self.paint)
    def set_image(self,path):
        picture=wx.Image(str(path))
        width,height=self.GetClientSize()
        picture.Rescale(max(1,width-20),max(1,height-40),wx.IMAGE_QUALITY_HIGH)
        self.bitmap=wx.Bitmap(picture);self.Refresh()
    def set_message(self,message):
        self.bitmap=None;self.message=message;self.Refresh()
    def paint(self,event):
        dc=wx.AutoBufferedPaintDC(self)
        dc.SetBackground(wx.Brush(CARD));dc.Clear()
        dc.SetTextForeground(MUTED);dc.DrawText('MODÈLE KI CAD  ·  VUE 3D',16,13)
        if self.bitmap:dc.DrawBitmap(self.bitmap,10,40,True)
        else:dc.DrawText(self.message[:65],16,85)

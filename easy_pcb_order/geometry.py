"""Board outline geometry in integer KiCad coordinates; no pcbnew dependency."""
import math

def area(poly):
    return abs(sum(x*y2-x2*y for (x,y),(x2,y2) in zip(poly,poly[1:]+poly[:1]))) / 2

def contains(point,poly):
    x,y=point
    inside=False
    for (ax,ay),(bx,by) in zip(poly,poly[1:]+poly[:1]):
        if (ay>y)!=(by>y) and x<(bx-ax)*(y-ay)/(by-ay)+ax:
            inside=not inside
    return inside

def arc_points(start,mid,end,max_step):
    """Approximate the sweep through mid; retain exact KiCad endpoints."""
    ax,ay=start; bx,by=mid; cx,cy=end
    d=2*(ax*(by-cy)+bx*(cy-ay)+cx*(ay-by))
    if abs(d)<1:
        raise ValueError('Arc Edge.Cuts dégénéré (trois points alignés).')
    q1=ax*ax+ay*ay; q2=bx*bx+by*by; q3=cx*cx+cy*cy
    ox=(q1*(by-cy)+q2*(cy-ay)+q3*(ay-by))/d
    oy=(q1*(cx-bx)+q2*(ax-cx)+q3*(bx-ax))/d
    radius=math.hypot(ax-ox,ay-oy)
    a=math.atan2(ay-oy,ax-ox)
    b=math.atan2(by-oy,bx-ox)
    c=math.atan2(cy-oy,cx-ox)
    ccw=(c-a)%(2*math.pi)
    sweep=ccw if (b-a)%(2*math.pi)<=ccw+1e-9 else ccw-2*math.pi
    # 5 degrees at most, with ~0.05 mm chord error on large radii.
    max_angle=min(math.pi/36,2*math.acos(max(-1,1-max_step/radius)))
    steps=max(2,math.ceil(abs(sweep)/max_angle))
    return [start]+[(ox+radius*math.cos(a+sweep*i/steps),oy+radius*math.sin(a+sweep*i/steps))
                    for i in range(1,steps)]+[end]

def circle_points(center,radius,max_step):
    if radius<=0:
        raise ValueError('Cercle Edge.Cuts de rayon nul.')
    max_angle=min(math.pi/36,2*math.acos(max(-1,1-max_step/radius)))
    steps=max(72,math.ceil(2*math.pi/max_angle))
    x,y=center
    return [(x+radius*math.cos(2*math.pi*i/steps),y+radius*math.sin(2*math.pi*i/steps)) for i in range(steps)]

def stitch(paths,tolerance):
    """paths: (polyline, original item). Circle paths are already closed."""
    def close(a,b): return abs(a[0]-b[0])<=tolerance and abs(a[1]-b[1])<=tolerance
    pending=list(paths)
    result=[]
    while pending:
        path,item=pending.pop(0)
        points=list(path); items=[item]
        while not close(points[-1],points[0]):
            matches=[]
            for i,(candidate,obj) in enumerate(pending):
                if close(candidate[0],points[-1]): matches.append((i,candidate,obj))
                elif close(candidate[-1],points[-1]): matches.append((i,list(reversed(candidate)),obj))
            if len(matches)!=1:
                raise ValueError('Contour Edge.Cuts ouvert, superposé ou ramifié près de %.2f, %.2f.' % points[-1])
            i,candidate,obj=matches[0]
            pending.pop(i); points.extend(candidate[1:]); items.append(obj)
        poly=points[:-1]
        if len(poly)<3 or area(poly)==0:
            raise ValueError('Contour Edge.Cuts dégénéré.')
        result.append({'poly':poly,'edges':items,'area':area(poly)})
    return result

def classify(contours):
    """Top level loops are boards; directly nested loops are their holes."""
    for c in contours:
        # A boundary vertex is a reliable interior witness for any containing loop.
        parents=[p for p in contours if p is not c and p['area']>c['area']
                 and contains(c['poly'][0],p['poly'])]
        if len(parents)>1:
            raise ValueError('Contours Edge.Cuts imbriqués sur plusieurs niveaux : vérifier les îlots.')
        c['parent']=min(parents,key=lambda p:p['area']) if parents else None
    boards=[]
    for c in contours:
        if c['parent'] is None:
            boards.append({'poly':c['poly'],'edges':list(c['edges']),'holes':[]})
        elif c['parent']['parent'] is not None:
            raise ValueError('Contour à l’intérieur d’une découpe : géométrie non prise en charge.')
    for board in boards:
        for c in contours:
            if c['parent'] is not None and c['parent']['poly'] is board['poly']:
                board['holes'].append(c['poly'])
                board['edges'].extend(c['edges'])
    boards.sort(key=lambda c:(min(p[0] for p in c['poly']),min(p[1] for p in c['poly'])))
    return boards

def owner(point,boards):
    matches=[i for i,b in enumerate(boards) if contains(point,b['poly'])
             and not any(contains(point,hole) for hole in b['holes'])]
    if len(matches)!=1:
        raise ValueError('Élément situé hors carte ou dans une découpe près de %.2f, %.2f.' % point)
    return matches[0]

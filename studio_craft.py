"""Conservative geometry rules for flat artwork, in physical millimetres.

These are starting rules for stable fabric, not anatomical fur directions or
fabric-calibrated production settings. Unsupported shapes stay as tatami fills.
"""
import math,itertools
from functools import lru_cache
import numpy as np
from shapely.geometry import Polygon, LineString
from shapely.affinity import affine_transform
from shapely.ops import unary_union
from scipy.ndimage import gaussian_filter1d
from studio_structure import converging_surface

# Starting values for flat, backed material. These are our trial presets, not
# manufacturer-certified recipes; changing fabric never silently changes the
# user's explicitly chosen top-row density.
FABRICS={
    'twill':{'name':'绣章布／斜纹布','pull_fill':.12,'pull_satin':.12,'inset':.4,'overlap':.18,'supportive':False},
    'felt':{'name':'毛毡／无纺布','pull_fill':.15,'pull_satin':.15,'inset':.35,'overlap':.20,'supportive':True},
    'knit':{'name':'衣服／针织面料','pull_fill':.20,'pull_satin':.18,'inset':.4,'overlap':.25,'supportive':True},
}


def main_axis(p):
    points=np.asarray(p.minimum_rotated_rectangle.exterior.coords)[:4]
    edges=np.roll(points,-1,axis=0)-points;lengths=np.linalg.norm(edges,axis=1)
    index=int(np.argmax(lengths));axis=edges[index]/max(lengths[index],1e-9)
    return axis,float(lengths[index]),float(min(lengths))


@lru_cache(maxsize=4096)
def fill_angle(p):
    axis,length,width=main_axis(p)
    if length/max(width,.01)<1.15:return 30
    # SVG y points down; Ink/Stitch's positive angle is counter-clockwise.
    return round((-math.degrees(math.atan2(axis[1],axis[0])))%180/5)*5%180


@lru_cache(maxsize=256)
def registered_tatami(angle, pitch, max_length):
    """Fit the along-row repeat and the next staggered row to the 0.1 mm export grid."""
    if angle % 90 == 0:
        return None
    radians = math.radians(-angle)
    desired = (math.cos(radians), math.sin(radians))
    candidates = []
    # Small primitive directions keep the along-row repeat within the stitch limit.
    for u, v in itertools.product(range(-4, 5), repeat=2):
        if not u or not v or math.gcd(abs(u), abs(v)) != 1:
            continue
        length = math.hypot(u, v)
        alignment = (u * desired[0] + v * desired[1]) / length
        if alignment < math.cos(math.radians(1.5)):
            continue
        repeat = math.floor(max_length / (.1 * length) + 1e-9) * .1 * length
        if repeat < .80 * max_length:
            continue
        # For an integer displacement to the next row its perpendicular
        # projection is the row pitch and its parallel projection is the stagger.
        reach = math.ceil((repeat / 3 + pitch) / .1)
        for dx, dy in itertools.product(range(-reach, reach + 1), repeat=2):
            spacing = .1 * (u * dy - v * dx) / length
            offset = .1 * (u * dx + v * dy) / length
            if not pitch - .012 <= spacing <= pitch + 1e-9 or offset <= 0:
                continue
            staggers = repeat / offset
            if 3 <= staggers <= 5.25:
                rank = (abs(pitch - spacing), abs(4 - staggers), -alignment)
                values = (-math.degrees(math.atan2(v, u)) % 180, spacing, repeat, staggers)
                candidates.append((rank, values))
    if candidates:
        return tuple(round(value, 7) for value in min(candidates)[1])
    return None


def round_group_directions(regions):
    """Aim similar, nearly round fills towards their group's centre on the machine grid."""
    by_color = {}
    for region in regions:
        shape = region['geometry']
        if shape.interiors or shape.area < 12 or shape.area < .94 * shape.convex_hull.area:
            continue
        _, length, width = main_axis(shape)
        if length <= 1.25 * width:
            by_color.setdefault(region['color'], []).append((shape.area, region['key'], shape.centroid))

    directions = {}
    for group in by_color.values():
        group.sort(key=lambda entry: entry[0])
        start = 0
        while start < len(group):
            end = start + 1
            while end < len(group) and group[end][0] <= 1.35 * group[start][0]:
                end += 1
            similar = group[start:end]
            start = end
            if len(similar) < 3:
                continue
            x = sum(c.x for _, _, c in similar) / len(similar)
            y = sum(c.y for _, _, c in similar) / len(similar)
            for _, key, centre in similar:
                directions[key] = 0 if abs(centre.x-x) >= abs(centre.y-y) else 90
    return directions


def protect_cloth_openings(needle_shape, visible, objects, index, tree, line_width, expansion):
    """Keep a tatami row's thread footprint and engine expansion out of bare fabric holes."""
    if not visible.interiors or len(visible.interiors) > 64:
        return needle_shape
    openings = []
    for ring in visible.interiors:
        hole = Polygon(ring)
        if hole.area < 4 * line_width**2:
            continue
        # Enclosed colour is a sewn inlay. Its own stitching and overlap need space.
        covered_area = sum(objects[j][0]['geometry'].intersection(hole).area
                           for j in tree.query(hole) if j != index)
        if covered_area <= line_width**2 / 4:
            openings.append(hole.buffer(.5 * line_width + max(0, expansion), quad_segs=6))
    if not openings:
        return needle_shape
    clipped = needle_shape.difference(unary_union(openings))
    if (clipped.geom_type != 'Polygon' or clipped.is_empty or not clipped.is_valid
            or len(clipped.interiors) != len(needle_shape.interiors)
            or visible.difference(clipped).area >= .04 * visible.area):
        return needle_shape
    return clipped


def restore_inlay_underlap(needle_shape, clipped, visible, later_fills, line_width):
    """Keep planned backing at an enclosed, sewn inset when its pale tip resembles a gap."""
    if not later_fills or not visible.interiors:
        return clipped
    tol = min(.04, line_width / 4)
    backed = []
    for ring in visible.interiors:
        if visible.exterior.distance(ring) < line_width:
            continue
        hole = Polygon(ring)
        if 2 * hole.area / hole.length < 2 * line_width:
            continue
        for fill in later_fills:
            if fill.interiors or abs(fill.area - hole.area) > tol * (hole.length + fill.length):
                continue
            # The inset must fill this particular hole. A line, the fabric
            # showing between letters, or an unrelated white object cannot qualify.
            if (hole.difference(fill.buffer(tol)).area > max(.005, .005 * hole.area)
                    or fill.difference(hole.buffer(tol)).area > max(.005, .005 * fill.area)):
                continue
            support = hole.buffer(line_width + math.hypot(.05, .05), quad_segs=4).intersection(visible.union(hole))
            backed.append(support)
            break
    if not backed:
        return clipped
    restored = clipped.union(needle_shape.intersection(unary_union(backed)))
    if restored.geom_type != 'Polygon' or not restored.is_valid or restored.area <= clipped.area + 1e-7:
        return clipped
    return restored


def machine_fill_geometry(p, line_width, row_spacing):
    """Limit fill-boundary error to a fraction of one row and one thread footprint."""
    tolerance=min(.05,line_width/4,row_spacing/4)
    if len(p.exterior.coords)+sum(len(r.coords) for r in p.interiors)<100:return p
    simplified=p.simplify(tolerance,preserve_topology=True)
    if (not simplified.is_valid or len(simplified.interiors)!=len(p.interiors)
            or p.symmetric_difference(simplified).area>p.area*.015):
        return p
    return simplified


def protect_fill_recesses(needle_shape, visible, line_width):
    """Reserve the thread footprint at a small motif's concave edge."""
    if visible.interiors or not 1 <= visible.area <= 45:
        return needle_shape
    recess = visible.convex_hull.difference(visible)
    if recess.area < .035 * visible.area or recess.buffer(-line_width / 2).area < .16:
        return needle_shape
    clipped = needle_shape.difference(recess.buffer(line_width * .65, quad_segs=5))
    if (clipped.geom_type != 'Polygon' or clipped.is_empty or not clipped.is_valid
            or len(clipped.interiors) != len(needle_shape.interiors)
            or visible.difference(clipped).area > visible.area * .12):
        return needle_shape
    return clipped


def supported_fill_edge(needle_shape, visible, earlier, line_width):
    """Keep the edge of a top fill on its side of previously sewn colour."""
    if not earlier or visible.area < 1:
        return needle_shape
    support = unary_union(earlier).buffer(line_width * .45, quad_segs=4)
    if visible.boundary.intersection(support).length < visible.length * .30:
        # A coloured recess can be local: reserve it even when most of this
        # edge borders cloth. Require an existing earlier colour inside.
        recess=visible.convex_hull.difference(visible).buffer(-line_width*.5,quad_segs=4)
        if recess.is_empty or support.intersection(recess).area < max(.36, 6*line_width**2, .30*recess.area):
            return needle_shape
    clipped = needle_shape.difference(support)
    if (clipped.geom_type != 'Polygon' or clipped.is_empty or not clipped.is_valid
            or len(clipped.interiors) != len(needle_shape.interiors)
            or visible.difference(clipped).area > visible.area * .12):
        return needle_shape
    return clipped


def directional_fill_margin(p, angle, distance):
    """Apply a pull allowance along the stitch rows, on the original vector boundary.

    Ink/Stitch 3.2 resamples the entire polygon when its tatami pull setting is
    nonzero. That can close or displace small windows at normal row spacings.
    A very flat ellipse approximates the horizontal segment sweep to <.004 mm.
    Preserve holes if even this allowance would close one.
    """
    if distance <= 0:
        return p
    theta = math.radians(angle)
    co, si = math.cos(theta), math.sin(theta)
    stretch = 120
    aligned = affine_transform(p, [co, -si, stretch*si, stretch*co, 0, 0])
    expanded = aligned.buffer(distance, quad_segs=4)
    result = affine_transform(expanded, [co, si/stretch, -si, co/stretch, 0, 0])
    result = result.simplify(min(.006, distance/3), preserve_topology=True)
    if result.geom_type != 'Polygon' or not result.is_valid or len(result.interiors) != len(p.interiors):
        return p
    return result


def rail_coverage(aa,bb):
    return unary_union([Polygon([aa[i],aa[i+1],bb[i+1],bb[i]]).buffer(0) for i in range(len(aa)-1)])


def rail_quality(aa,bb):
    """Diagnostic geometry, not a fabric coverage or sew-out certificate."""
    a,b=np.asarray(aa),np.asarray(bb)
    advances=np.stack([np.linalg.norm(np.diff(r,axis=0),axis=1) for r in (a,b)])
    ratio=advances.min(0)/np.maximum(advances.max(0),1e-9)
    angles=np.unwrap(np.arctan2((b-a)[:,1],(b-a)[:,0]))
    return {'stations':len(a),'stationary_steps':int((advances<.01).sum()),
            'imbalanced_steps':int((ratio<.2).sum()),
            'max_turn_deg':round(float(np.max(np.abs(np.diff(angles)))*180/math.pi),2)}


def inlaid_satin_band(left, right, widths, line_width, tree, colors, position, compact=False):
    """Find long ribbons with sewn, differently coloured neighbours along both rails."""
    median = float(np.median(widths))
    if not max(.55, 2.5 * line_width) <= median <= 1.65:
        return False
    a, b = np.asarray(left), np.asarray(right)
    center = (a + b) / 2
    stations = np.r_[0, np.cumsum(np.linalg.norm(np.diff(center, axis=0), axis=1))]
    # Short inlaid bars still have two supported rails. Do not use this
    # relaxation on a standalone spot or a bar with a free side.
    if stations[-1] < (max(2., 2.0*median) if compact else max(3.,3.5*median)):
        return False
    # Sample equal distances along the ribbon so a busy corner cannot dominate
    # the decision. A free edge, an opening or a later inlay supplies no backing.
    from shapely import points
    samples = stations[-1] * np.linspace(.12, .88, 9)
    for rail in (a, b):
        probes = points(np.column_stack([np.interp(samples, stations, v) for v in rail.T]))
        sample, neighbour = tree.query(probes, predicate='dwithin', distance=.75 * line_width)
        supported = (neighbour < position) & (colors[neighbour] != colors[position])
        if len(np.unique(sample[supported])) < 6:
            return False
    return True


def one_sided_satin_rails(left,right,widths,line,pull,tree,colors,position):
    """Retain pull on the free rail, reserve the thread footprint at a sewn seam."""
    from shapely import points
    a,b=np.asarray(left),np.asarray(right)
    widths=np.asarray(widths)
    if len(a)<6 or not 1.35<=np.median(widths)<=4:return None
    across=(b-a)/np.maximum(np.linalg.norm(b-a,axis=1),.001)[:,None]
    masks=[]
    for rail,direction in ((a,-across),(b,across)):
        # Probe just outside the columns. A neighbouring later colour does
        # not provide support for the current pass.
        samples,neighbours=tree.query(points(rail+.4*line*direction),predicate='dwithin',distance=.4*line)
        hits=np.zeros(len(rail),bool)
        hits[samples[(neighbours<position)&(colors[neighbours]!=colors[position])]]=True
        segments=np.linalg.norm(np.diff(rail,axis=0),axis=1)
        share=float(segments[hits[:-1]&hits[1:]].sum()/max(.001,segments.sum()))
        masks.append((hits,share))
    active=max(range(2),key=lambda i:masks[i][1])
    if masks[active][1]<.65 or masks[1-active][1]>.20:return None
    # Taper into a join. A free tip and the opposite rail keep their original endpoints.
    weights=np.convolve(np.pad(masks[active][0].astype(float),1,mode='edge'),[.2,.6,.2],mode='valid')
    offset=np.minimum(.22*widths,pull+.5*line)*weights
    return ((a+offset[:,None]*across,b) if active==0 else (a,b-offset[:,None]*across))


def fair_frame_rails(p,aa,bb,inner):
    """Spread nearest-point stalls along the SAME boundary; keep both ends.

    Smooth arc-length correspondence, never the artwork outline. Monotonic
    progression prevents repeated penetrations aimed at one inner corner.
    Keep the original pairing if the proposed quads spill or lose coverage.
    The constants here are geometric trial tolerances, not density settings.
    """
    a,b=np.asarray(aa),np.asarray(bb)
    from shapely.geometry import Point
    distance=np.linalg.norm(np.diff(a,axis=0),axis=1)
    positions=np.array([inner.project(Point(q)) for q in b])
    positions=np.unwrap(positions*2*math.pi/inner.length)*inner.length/(2*math.pi)
    direction=1 if positions[-1]>positions[0] else -1
    positions*=direction
    span=positions[-1]-positions[0]
    if span<.1 or np.min(np.diff(positions))<-.3:return aa,bb,np.linalg.norm(a-b,axis=1)
    sigma=min(2.5,max(.6,.35/max(float(np.median(distance)),.05)))
    target=gaussian_filter1d(positions,sigma,mode='nearest')
    target+=np.linspace(positions[0]-target[0],positions[-1]-target[-1],len(target))
    advance=np.maximum(np.diff(target),distance*.18)
    advance*=span/advance.sum()
    target=positions[0]+np.r_[0,np.cumsum(advance)]
    candidate=np.array([inner.interpolate((u*direction)%inner.length).coords[0] for u in target])
    widths=np.linalg.norm(a-candidate,axis=1)
    old_widths=np.linalg.norm(a-b,axis=1)
    if widths.max()>5.5 or np.max(np.linalg.norm(candidate-b,axis=1))>min(.5,np.median(old_widths)*.4):
        return aa,bb,old_widths
    old=rail_coverage(a,b);new=rail_coverage(a,candidate)
    if (new.difference(p.buffer(.08)).area>max(.01,new.area*.005) or
            old.difference(new.buffer(.06)).area>max(.02,old.area*.005)):
        return aa,bb,old_widths
    # A monotone boundary mapping can still cross on an unusually concave band.
    from shapely.strtree import STRtree
    rungs=[LineString([x,y]) for x,y in zip(a,candidate)];tree=STRtree(rungs)
    if any(rung.crosses(rungs[j]) for i,rung in enumerate(rungs) for j in tree.query(rung) if j>i):
        return aa,bb,old_widths
    return aa,candidate.tolist(),widths


def curved_rails(p):
    """A curved, unbranched narrow band may use turning satin, not flat fill.

    The medial skeleton proposes a centerline; cross-sections and coverage
    checks decide whether it is safe. A T/Y branch or a wide region falls back
    to fill. No species-specific recognition or generated coordinates.
    """
    if p.interiors or p.area<.2 or 2*p.area/max(p.length,.01)>2.6:return None
    import heapq
    from skimage.morphology import skeletonize
    from shapely import contains_xy
    from shapely.geometry import Point
    x0,y0,x1,y1=p.bounds
    pixel=max(.055,max(x1-x0,y1-y0)/600)
    xs=np.arange(x0-pixel,x1+2*pixel,pixel);ys=np.arange(y0-pixel,y1+2*pixel,pixel)
    xx,yy=np.meshgrid(xs,ys)
    points=np.argwhere(skeletonize(contains_xy(p,xx,yy)))
    if len(points)<8:return None
    lookup={tuple(q):i for i,q in enumerate(points)};graph=[[] for _ in points]
    for i,(y,x) in enumerate(points):
        for dy,dx in itertools.product((-1,0,1),repeat=2):
            if not (dy or dx):continue
            j=lookup.get((y+dy,x+dx))
            if j is not None:graph[i].append((j,math.hypot(dx,dy)))
    ends=[i for i,g in enumerate(graph) if len(g)==1]
    if not 2<=len(ends)<=16:return None
    def distances(start):
        best={start:0.};previous={};queue=[(0.,start)]
        while queue:
            d,i=heapq.heappop(queue)
            if d>best[i]:continue
            for j,w in graph[i]:
                if d+w<best.get(j,math.inf):
                    best[j]=d+w;previous[j]=i;heapq.heappush(queue,(d+w,j))
        return best,previous
    initial,_=distances(ends[0]);start=max(ends,key=lambda i:initial.get(i,-1))
    best,previous=distances(start);end=max(ends,key=lambda i:best.get(i,-1))
    indices=[end]
    while indices[-1]!=start:indices.append(previous[indices[-1]])
    center=np.array([[xs[points[i,1]],ys[points[i,0]]] for i in indices[::-1]])
    center=gaussian_filter1d(center,max(1,.18/pixel),axis=0,mode='nearest')
    line=LineString(center)
    if line.length<1.35:return None
    # Extend both ends to the outline so a skeleton's inset cap is not lost.
    def endpoint(first):
        tip=center[0] if first else center[-1]
        reference=line.interpolate(min(.7,line.length/4) if first else max(0,line.length-.7))
        direction=tip-np.array(reference.coords[0]);direction/=max(np.linalg.norm(direction),1e-9)
        ray=LineString([tip,tip+direction*6]);part=ray.intersection(p)
        parts=list(part.geoms) if hasattr(part,'geoms') else [part]
        part=next((q for q in parts if q.geom_type=='LineString' and q.distance(Point(tip))<pixel*2),None)
        if part is None:return tip
        return np.array(max(part.coords,key=lambda q:Point(q).distance(Point(tip))))
    line=LineString([endpoint(True),*center,endpoint(False)])
    stations=np.linspace(.025,line.length-.025,max(12,int(line.length/.22)))
    aa=[];bb=[]
    for d in stations:
        c=np.array(line.interpolate(d).coords[0])
        tangent=np.array(line.interpolate(min(line.length,d+.22)).coords[0])-np.array(line.interpolate(max(0,d-.22)).coords[0])
        tangent/=max(np.linalg.norm(tangent),1e-9);normal=np.array([-tangent[1],tangent[0]])
        section=p.intersection(LineString([c-normal*6,c+normal*6]))
        parts=list(section.geoms) if hasattr(section,'geoms') else [section]
        section=next((q for q in parts if q.geom_type=='LineString' and q.distance(Point(c))<pixel*2),None)
        if section is None:return None
        coordinates=np.asarray(section.coords);projection=(coordinates-c)@normal
        aa.append(coordinates[projection.argmin()]);bb.append(coordinates[projection.argmax()])
    a,b=np.asarray(aa),np.asarray(bb);widths=np.linalg.norm(a-b,axis=1)
    if widths.max()>5.5 or np.median(widths)<.14:return None
    from shapely.strtree import STRtree
    rungs=[LineString([x,y]) for x,y in zip(a,b)];tree=STRtree(rungs)
    # Pixel stair-steps on the inside of a bend can reverse a single pair of
    # neighbouring stations. Keep the end caps and reject folds across a bend.
    drop=set()
    for i,rung in enumerate(rungs):
        for j in tree.query(rung):
            if j<=i or not rung.crosses(rungs[j]):continue
            if j!=i+1:return None
            drop.add(j if j<len(a)-1 else i)
    if drop:
        if len(drop)>max(1,len(a)//12) or any(i+1 in drop for i in drop):return None
        a,b,widths=(np.delete(values,sorted(drop),axis=0) for values in (a,b,widths))
    coverage=rail_coverage(a,b)
    if (coverage.difference(p.buffer(.06)).area>max(.02,p.area*.005) or
            p.difference(coverage.buffer(.06)).area>max(.08,p.area*.025)):
        return None
    return a.tolist(),b.tolist(),widths


def covered_stitch_region(p, later, overlap=.18):
    """Keep visual artwork intact while allowing stitches underneath later color.

    Only small, almost fully covered holes are closed. Larger cutouts retain
    their centers; genuine transparent holes and exposed edges never expand.
    The 0.04 mm tolerance bridges tracing gaps, not arbitrary empty regions.
    """
    details={'closed_detail_holes':0,'underlap_mm':0.,'added_area_mm2':0.}
    if not later:return p,details
    cover=unary_union(later)
    close_cover=cover.buffer(.04)
    holes=[]
    for ring in p.interiors:
        hole=Polygon(ring)
        _,_,width=main_axis(hole)
        if (hole.area<=6 and width<=3.2 and
            hole.difference(close_cover).area<=max(.01,hole.area*.01)):
            details['closed_detail_holes']+=1
        else:holes.append(ring.coords)
    base=Polygon(p.exterior,holes)
    # The extension is restricted to a later object's footprint, so a free
    # exterior edge cannot grow merely because it is near the same color.
    result=base.union(base.buffer(overlap).intersection(close_cover)).buffer(0)
    if result.geom_type!='Polygon' or not result.is_valid:return p,{'closed_detail_holes':0,'underlap_mm':0.,'added_area_mm2':0.}
    details['added_area_mm2']=round(result.area-p.area,3)
    details['underlap_mm']=overlap if result.area-base.area>.01 else 0.
    return result,details


def split_exposed_bands(visible, needle_shape, line, exposed):
    """Separate long satin-width ends attached to an otherwise broad fill."""
    if visible.area<8 or visible.geom_type!='Polygon':return None
    radius=1.75*line
    opened=visible.buffer(-radius,quad_segs=4).buffer(radius+.05,quad_segs=4)
    if opened.is_empty:return None
    fringe=visible.difference(opened)
    parts=getattr(fringe,'geoms',(fringe,))
    if len(parts)>96:return None
    bands=[]
    for part in parts:
        if part.geom_type!='Polygon' or part.area<max(.6,14*line**2):continue
        _,length,width=main_axis(part)
        if length<10*line or length<3*width or not exposed(part):continue
        # A free stem may end in a small round cap. Reject long seams.
        joins=part.boundary.intersection(opened.boundary)
        contacts=joins.buffer(.15*line)
        joints=getattr(contacts,'geoms',(contacts,))
        if joins.is_empty or len(joints)>2 or any(j.convex_hull.length>12*line for j in joints):continue
        sections,_=satin_candidate(part)
        if not sections or len(sections)!=1:continue
        widths=sections[0][2];median=float(np.median(widths))
        if 2*line<=median<=4*line and length>=4*median:bands.append(part)
    if not bands:return None
    ends=unary_union(bands)
    solid=visible.difference(ends)
    if solid.area<visible.area*.6:return None
    # Give each joint a half-thread of underlap; retain earlier underlap at
    # other colour boundaries and keep transparent openings in the body.
    body=needle_shape.difference(ends.buffer(.015,quad_segs=4).difference(solid.buffer(.5*line,quad_segs=4)))
    pieces=sorted(getattr(body,'geoms',(body,)),key=lambda p:-p.area)
    if not pieces or not body.is_valid or any(p.geom_type!='Polygon' for p in pieces):return None
    body,*caps=pieces
    if body.area<.6*visible.area or len(body.interiors)!=len(needle_shape.interiors):return None
    for cap in caps:
        if cap.interiors or cap.area>100*line**2 or cap.area<.8*cap.convex_hull.area:return None
        touching=[i for i,band in enumerate(bands) if band.intersects(cap)]
        if len(touching)!=1:return None
        i=touching[0];whole=bands[i].union(cap)
        if whole.geom_type!='Polygon' or whole.interiors:return None
        rails,_=satin_candidate(whole)
        if not rails or len(rails)!=1 or np.median(rails[0][2])<2*line:return None
        bands[i]=whole
    return body,bands


def narrow_rails(p):
    """Cross-sections are accepted only for a single, narrow, unbranched strip."""
    if p.interiors or p.area<.2:return None
    axis,length,width=main_axis(p)
    # Tiny closed spots (such as 1–2 mm eyes) can also be short satin columns.
    # At that size a long-stitch tatami pattern leaves very few surface rows.
    compact_detail=p.area<16 and width<3.6
    if (length/max(width,.01)<2 and not compact_detail) or 2*p.area/max(p.length,.01)>2.6:return None
    normal=np.array([-axis[1],axis[0]]);origin=np.asarray(p.centroid.coords[0])
    coords=np.asarray(p.exterior.coords)-origin
    lo,hi=float((coords@axis).min()),float((coords@axis).max())
    # A sub-millimetre capsule may have a jagged, one-pixel cap. Sample
    # through its solid interior and keep the usual footprint checks below.
    compact=length<=2.4 and width<=1.0 and p.area>=p.convex_hull.area*.90
    cap=min(.08,width*.15) if compact else .015
    slice_shape=p.convex_hull if compact else p
    aa=[];bb=[]
    for distance in np.linspace(lo+cap,hi-cap,max(10,int(length/.3))):
        center=origin+axis*distance
        section=slice_shape.intersection(LineString([center-normal*(length+width),center+normal*(length+width)]))
        if section.geom_type!='LineString' or section.length<.015:return None
        points=np.asarray(section.coords);projection=(points-center)@normal
        aa.append(tuple(points[int(np.argmin(projection))]));bb.append(tuple(points[int(np.argmax(projection))]))
    widths=np.linalg.norm(np.asarray(aa)-np.asarray(bb),axis=1)
    if max(widths)>5.5 or np.median(widths)<.14:return None
    coverage=rail_coverage(aa,bb)
    if coverage.difference(p.buffer(.08)).area>p.area*.02:return None
    if p.difference(coverage.buffer(.08)).area>max(.08,p.area*.025):return None
    return aa,bb,widths


def ring_rails(p):
    if len(p.interiors)!=1 or Polygon(p.interiors[0]).area<Polygon(p.exterior).area*.45:return None
    outer=LineString(p.exterior.coords);inner=LineString(p.interiors[0].coords)
    aa=[];bb=[];widths=[]
    # Open the rails at a small seam; do not feed a closed rail into the satin tool.
    for distance in np.linspace(0,outer.length-.04,max(80,int(outer.length/.45))):
        a=outer.interpolate(distance);b=inner.interpolate(inner.project(a))
        aa.append((a.x,a.y));bb.append((b.x,b.y));widths.append(a.distance(b))
    if max(widths)>5.5 or np.median(widths)<.35:return None
    coverage=rail_coverage(aa,bb)
    if coverage.difference(p.buffer(.2)).area>p.area*.12:return None
    return fair_frame_rails(p,aa,bb,inner)


def frame_rails(p):
    """Split an existing multi-window frame into valid outer satin arcs.

    Each arc follows one inner contour. At a change of window we split the
    column instead of stitching straight across the differently colored hole.
    Internal bridges are left for separate stitches, with no invented border.
    """
    # A highly perforated ornament is not a satin column. Trying every hole
    # against every station is both ambiguous and quadratic in photo detail.
    if len(p.interiors)>12:return None
    if len(p.interiors)==1:
        rails=ring_rails(p)
        return [rails] if rails is not None else None
    if len(p.interiors)<2 or sum(Polygon(q).area for q in p.interiors)<Polygon(p.exterior).area*.45:return None
    outer=LineString(p.exterior.coords);inners=[LineString(q.coords) for q in p.interiors]
    count=max(120,int(outer.length/.25));step=outer.length/count
    labels=[min(range(len(inners)),key=lambda j:outer.interpolate(i*step).distance(inners[j])) for i in range(count)]
    start=next((i for i in range(count) if labels[i]!=labels[i-1]),None)
    if start is None:return None
    result=[]
    for label,items in itertools.groupby(range(start,start+count),key=lambda i:labels[i%count]):
        indices=list(items)
        if len(indices)<3:return None
        distances=[(indices[0]-.5)*step]+[i*step for i in indices]+[(indices[-1]+.5)*step]
        aa=[];bb=[]
        for distance in distances:
            a=outer.interpolate(distance%outer.length);b=inners[label].interpolate(inners[label].project(a))
            aa.append((a.x,a.y));bb.append((b.x,b.y))
        widths=np.linalg.norm(np.asarray(aa)-np.asarray(bb),axis=1)
        if max(widths)>5.5 or np.median(widths)<.35:return None
        coverage=rail_coverage(aa,bb)
        if coverage.difference(p.buffer(.08)).area>max(.02,coverage.area*.01):return None
        result.append(fair_frame_rails(p,aa,bb,inners[label]))
    return result


@lru_cache(maxsize=512)
def branched_ring_rails(p):
    """Trace a closed band that has stroke ends attached on the outside.

    The inner edge supplies the full course of the column. Measure the visible
    band around the opening and trim off projecting branches at their joins.
    A plate with an incidental hole does not have a narrow pair of matching
    edges around most of the opening.
    """
    if len(p.interiors) != 1:
        return None
    window = Polygon(p.interiors[0])
    if not .20 <= window.area / (window.area + p.area) <= .85:
        return None
    inner, outer = window.exterior, p.exterior
    count = min(300, max(32, math.ceil(inner.length / .25)))
    distances = [inner.interpolate(k / count, normalized=True).distance(outer) for k in range(count)]
    lower, width, upper = np.quantile(distances, [.12, .5, .85])
    if not .55 <= width <= 2.4 or lower < width * .74 or upper > width * 1.32:
        return None
    band = window.buffer(width + min(.18, width * .14), quad_segs=12).intersection(p)
    if band.geom_type != 'Polygon' or len(band.interiors) != 1:
        return None
    tails = p.difference(band)
    parts = [q for q in getattr(tails, 'geoms', [tails]) if q.area > width * width * .45]
    if not 1 <= len(parts) <= 4 or not .07 <= sum(q.area for q in parts) / p.area <= .44:
        return None
    if any(2 * q.area / q.length > 1.6 * width for q in parts):
        return None
    rails = ring_rails(band)
    if rails is None:
        return None
    a, b, _ = rails
    sewn = rail_coverage(a, b)
    if (sewn.intersection(window.buffer(-.035)).area > .02 or
            band.difference(sewn.buffer(.06)).area > max(.06, band.area * .015)):
        return None
    return rails


@lru_cache(maxsize=2048)
def satin_candidate(p):
    """Shared geometry decision for the editor and the machine planner."""
    rim = branched_ring_rails(p)
    if rim is not None:return [rim],'闭合轮廓与分支'
    sections=frame_rails(p)
    if sections is not None:return sections,'轮廓随形'
    rails=narrow_rails(p);construction='直向窄带'
    if rails is not None:
        mid=(np.asarray(rails[0])+np.asarray(rails[1]))/2
        center=LineString(mid)
        deviation=center.hausdorff_distance(LineString([mid[0],mid[-1]]))
        if deviation<=.035:return [rails],construction
    curved=curved_rails(p)
    if curved is not None:
        direction=np.asarray(curved[1])-np.asarray(curved[0])
        turn=np.ptp(np.unwrap(np.arctan2(direction[:,1],direction[:,0])))*180/math.pi
        if rails is None or turn>35:rails=curved;construction='弯曲窄带'
    return ([rails],construction) if rails is not None else (None,None)

@lru_cache(maxsize=1024)
def fine_line(p):
    """Accept a continuous, unbranched line whose centre can be sewn in one pass."""
    if p.geom_type!='Polygon' or p.is_empty:return None
    nominal=2*p.area/max(p.length,.01)
    if nominal>.55 or nominal<.12:return None
    if p.interiors:
        if len(p.interiors)!=1:return None
        hole=Polygon(p.interiors[0])
        if hole.area<2 or main_axis(hole)[2]<1:return None
        outer=LineString(p.exterior.coords);inner=LineString(p.interiors[0].coords)
        stations=np.linspace(0,outer.length,max(16,math.ceil(outer.length/.14)),endpoint=False)
        left=[outer.interpolate(s) for s in stations]
        right=[inner.interpolate(inner.project(q)) for q in left]
        widths=np.array([a.distance(b) for a,b in zip(left,right)])
        width=float(np.median(widths))
        if not .14<=width<.55 or np.quantile(widths,.90)>width*1.7:return None
        mid=[((a.x+b.x)/2,(a.y+b.y)/2) for a,b in zip(left,right)]
        line=LineString([*mid,mid[0]]).simplify(min(.035,width*.12))
        if not line.is_ring or not p.buffer(.015).covers(line):return None
        if p.difference(line.buffer(width*.75)).area>p.area*.08:return None
        return line,width
    sections,_=satin_candidate(p)
    if sections is None or len(sections)!=1:return None
    left,right,widths=sections[0]
    width=float(np.median(widths))
    if width>=.55 or np.quantile(widths,.90)>width*1.7:return None
    line=LineString((np.asarray(left)+np.asarray(right))/2).simplify(min(.035,width*.12))
    if line.length<max(1.35,width*4) or not p.buffer(.015).covers(line):return None
    if p.difference(line.buffer(width*.75)).area>p.area*.08:return None
    return line,width


def inset_details(groups,color_order):
    """Find small inlays bounded by another colour, regardless of hue."""
    from shapely.strtree import STRtree
    windows=[];hosts=[]
    for ci,group in enumerate(groups):
        for host in group:
            for ring in host.interiors:
                hole=Polygon(ring)
                if hole.area<=16:
                    windows.append(hole);hosts.append((ci,host.area))
    result=set();rank={ci:i for i,ci in enumerate(color_order)}
    if not windows:return result
    tree=STRtree(windows)
    for ci,group in enumerate(groups):
        for i,p in enumerate(group):
            if p.area>16:continue
            for index in tree.query(p):
                owner,area=hosts[index];hole=windows[index]
                if rank[owner]<=rank[ci] or area<3*p.area or hole.area>p.area*1.4+.08:continue
                if hole.intersection(p).area>=p.area*.90:
                    result.add((ci,i));break
    return result


@lru_cache(maxsize=1024)
def narrow_surface(p, line_width):
    """Recognise a small closed motif or flared band that a centre run cannot cover."""
    if p.interiors or fine_line(p) is not None:
        return False
    sections, _ = satin_candidate(p)
    if not sections or len(sections) != 1:
        return False
    widths = sections[0][2]
    median = float(np.median(widths))
    return max(.30, 1.5*line_width) <= median < .55 and np.quantile(widths, .75) >= 1.95*line_width


def small_round_spot(p, line_width):
    if p.geom_type!='Polygon' or p.interiors:return False
    diameter=math.sqrt(4*p.area/math.pi)
    return 4*line_width<=diameter<=10*line_width and 4*math.pi*p.area>=.90*p.length**2


@lru_cache(maxsize=2048)
def recommend_stitch(p, line_width=.24):
    """Describe the visible structure before consulting density or local overrides."""
    if p.geom_type!='Polygon':
        return {'structure':'surface','method':'fill','label':'填针','reason':'分开的色面按各自边界填针。','angle':fill_angle(p)}
    detail=fine_line(p)
    if detail is not None:
        center,width=detail
        chord=LineString([center.coords[0],center.coords[-1]])
        straight=(not center.is_ring and center.hausdorff_distance(chord)<max(.06,.25*width))
        if straight and width>=2.0*line_width:
            return {'structure':'band','method':'satin','label':'窄缎面针','width_mm':round(width,2),
                    'reason':'近等宽的直色带能容纳横跨两边的针迹，保留带宽。'}
        return {'structure':'line','method':'run' if width<.30 else 'running','label':'单针走针' if width<.30 else '三重走针',
                'reason':'沿连续细线的中心走针，转弯跟随原轮廓。','width_mm':round(width,2)}
    if converging_surface(p):
        return {'structure':'spread','method':'spread','label':'随展开方向分区填针',
                'reason':'两侧向同一处收拢，外缘逐渐展开。尖端单独绣一次，宽面分区逐步转向。'}
    sections,construction=satin_candidate(p)
    if sections is None or np.median(sections[0][2])<.30:
        return {'structure':'surface','method':'fill','label':'填针','reason':'按面状处理，检查本色覆盖与留白。若图案有特定纹理，可画方向曲线。','angle':fill_angle(p)}
    width=float(np.median(sections[0][2]))
    if width<.55 and not p.interiors and len(sections)==1:
        if narrow_surface(p, line_width):
            return {'structure':'band','method':'satin','label':'缎面针','reason':'这里有可辨宽度，针迹横跨两侧，保留花瓣或渐宽窄带的轮廓。','width_mm':round(width,2)}
        return {'structure':'line','method':'running','label':'三重走针','reason':'细窄线条沿中心线往返，保留线条形状。','width_mm':round(width,2)}
    # A small dot can legitimately take short fill rows or satin. A true band
    # or enclosing rim has two identifiable sides and a direction to preserve.
    axis_length=main_axis(p)[1]
    structure='band' if p.interiors or axis_length>=3*width else 'spot'
    return {'structure':structure,'method':'satin','label':'缎面针','reason':construction+'，跨越两侧轮廓排针；底针按列宽安排。','width_mm':round(width,2)}


def stroke_center(p):
    detail=fine_line(p)
    if detail is not None:return detail[0]
    sections,_=satin_candidate(p)
    if sections and len(sections)==1:
        left,right,_=sections[0]
        return LineString((np.asarray(left)+np.asarray(right))/2).simplify(.025)
    return None


def region_recommendations(regions, line_width):
    """Look for repeated, elongated surfaces with axes meeting at one centre."""
    decisions={r['key']:recommend_stitch(r['geometry'],line_width) for r in regions}
    if not 3<=len(regions)<=160:return decisions
    from shapely.strtree import STRtree
    candidates=[]
    for region in regions:
        p=region['geometry']
        if decisions[region['key']]['structure']!='surface' or p.geom_type!='Polygon' or p.interiors or p.area<8:continue
        axis,length,width=main_axis(p)
        if width>=2.6 and 1.4<=length/width<=6 and p.area>=.90*p.convex_hull.area:
            candidates.append((region,p.centroid,axis,length))
    if len(candidates)<3:return decisions
    tree=STRtree([item[1] for item in candidates]);examined=set()
    for r,center,_,length in candidates:
        shape=r['geometry']
        group=tuple(sorted(j for j in tree.query(center,predicate='dwithin',distance=length*3)
                           if candidates[j][0]['color']==r['color']
                           and .70<=candidates[j][0]['geometry'].area/shape.area<=1.43))
        if not 3<=len(group)<=24 or group in examined:continue
        examined.add(group)
        axes=np.array([candidates[j][2] for j in group]);normals=np.column_stack((-axes[:,1],axes[:,0]))
        centers=np.array([candidates[j][1].coords[0] for j in group])
        # Parallel bands have a direction but do not establish a shared root.
        if np.linalg.cond(normals)>3:continue
        origin=np.linalg.lstsq(normals,np.sum(normals*centers,axis=1),rcond=None)[0]
        outward=centers-origin;dist=np.linalg.norm(outward,axis=1)
        lengths=np.array([candidates[j][3] for j in group])
        if np.any(dist<.40*lengths) or np.any(dist>2.2*lengths):continue
        along=np.abs(np.sum(axes*outward,axis=1)/dist)
        if min(along)<math.cos(math.radians(16)):continue
        for j,vector in zip(group,outward):
            key=candidates[j][0]['key']
            decisions[key]={'structure':'axis','method':'fill','label':'顺纹填针',
                            'angle':round((-math.degrees(math.atan2(vector[1],vector[0])))%180,2),
                            'reason':'同组细长色面的长轴指向同一中心，每块沿自己的伸展方向填针。'}
    return decisions


def satin_underlay(width, upper_width=None, supportive=False):
    # Official underlay examples: center for narrow columns, contour for medium,
    # contour + zigzag for wide columns. No claim of a universal density recipe.
    # A flared column can have a narrow median but a much wider working area.
    # Its 75th-percentile width decides whether contour support is needed.
    support_width=max(width,upper_width or width)
    return {'center_walk_underlay':width<2,
            'contour_underlay':support_width>=(1.5 if supportive else 2),
            'zigzag_underlay':support_width>=(2.5 if supportive else 4),
            'center_walk_underlay_repeats':2,
            'center_walk_underlay_stitch_length_mm':2,
            'contour_underlay_stitch_length_mm':2,
            'contour_underlay_inset_mm':.4,
            'zigzag_underlay_spacing_mm':1.8,
            'zigzag_underlay_inset_mm':.2,
            'zigzag_underlay_max_stitch_length_mm':4}


def satin_short_distance(peak_spacing):
    # Shorten a crowded rail, not every other stitch on an ordinary straight
    # rail. A fixed 0.25 mm threshold scallops the edge at 0.20 mm peak spacing.
    return round(min(.25,peak_spacing*.6),3)


def outer_borders(groups):
    """Recognize an existing enclosing border; never invent a new outline."""
    shapes=[p for group in groups for p in group]
    if not shapes:return set(),None
    exterior_area=max(Polygon(p.exterior).area for p in shapes)
    borders=set();inner_edges=[]
    for ci,group in enumerate(groups):
        for i,p in enumerate(group):
            if Polygon(p.exterior).area>=exterior_area*.85 and frame_rails(p) is not None:
                borders.add((ci,i));inner_edges.extend(LineString(q.coords) for q in p.interiors)
    if not borders:return set(),None
    edge=unary_union(inner_edges)
    contacts={ci:edge.intersection(unary_union([p for i,p in enumerate(group) if (ci,i) not in borders]).buffer(.18)).length
              for ci,group in enumerate(groups) if any((ci,i) not in borders for i in range(len(group)))}
    background=max(contacts,key=contacts.get) if contacts else None
    return borders,background

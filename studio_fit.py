"""Size-aware artwork preparation. Trial rules; never a physical sew certificate.

Only a new version is written. Removed interior decoration is reassigned to a
neighbouring colour, not made transparent. No model call is needed.
"""
from pathlib import Path
import copy
import cv2
import numpy as np
from PIL import Image
from scipy.ndimage import distance_transform_edt
from studio_artwork import extract_geometries, save_json, artwork_view, write_svg
from studio_feasibility import approximate_width, thread_detail_limits
from studio_craft import fine_line, main_axis, satin_candidate
from shapely.geometry import Polygon, LineString
from shapely.ops import unary_union
from scipy.spatial import cKDTree

POLICY = 'fit-v7-physical-lines'


def polygon_mask(p, shape, scale, origin=(0, 0)):
    mask = np.zeros(shape, np.uint8)
    def points(ring):
        return np.rint((np.asarray(ring.coords) - 5) / scale).astype(np.int32) - np.asarray(origin, np.int32)
    parts = list(p.geoms) if p.geom_type == 'MultiPolygon' else [p]
    for part in parts:
        if part.is_empty or part.geom_type != 'Polygon':
            continue
        cv2.fillPoly(mask, [points(part.exterior)], 1)
        for hole in part.interiors:
            cv2.fillPoly(mask, [points(hole)], 0)
    return mask.astype(bool)


def pixel_window(shape, x, y, width, height, pad):
    return (slice(max(0,y-pad), min(shape[0],y+height+pad)),
            slice(max(0,x-pad), min(shape[1],x+width+pad)))


def join_pixel_tips(classes, scale, line, colors):
    """Attach a diagonal one-pixel tip to its own solid colour by one edge.

    Marching squares otherwise sees an enclosed eyelet on the neighbouring
    colour. Never close an actual transparent hole, bridge two detached
    marks, or delete a coloured highlight.
    """
    if scale>line*.8:return classes,[]
    result=classes.copy(); changes=[]
    cross=np.array([[0,1,0],[1,0,1],[0,1,0]],np.float32)
    diag=np.array([[1,0,1],[0,0,0],[1,0,1]],np.float32)
    for ci in range(colors):
        mask=(classes==ci).view(np.uint8)
        edges=cv2.filter2D(mask,-1,cross,borderType=cv2.BORDER_CONSTANT)
        neighbours=cv2.filter2D(mask,-1,diag,borderType=cv2.BORDER_CONSTANT)
        near_body=cv2.filter2D((mask.astype(bool)&(edges>=1)).view(np.uint8),-1,diag,borderType=cv2.BORDER_CONSTANT)
        solid=cv2.filter2D(mask,-1,np.ones((5,5),np.float32),borderType=cv2.BORDER_CONSTANT)
        ys,xs=np.nonzero(mask&(edges==0)&(neighbours==1)&(near_body==1)&(solid>=4))
        for y,x in zip(ys,xs):
            if min(y,x)==0 or y+1>=mask.shape[0] or x+1>=mask.shape[1]:continue
            tile=classes[y-1:y+2,x-1:x+2];other=np.unique(tile[tile!=ci])
            if len(other)!=1 or other[0]==255 or np.any(result[y-1:y+2,x-1:x+2]!=tile):continue
            yy,xx=np.nonzero((tile==ci)&diag.astype(bool));dy,dx=int(yy[0])-1,int(xx[0])-1
            # Prefer the side supported by more of the same ink.
            yy,xx=max(((y+dy,x),(y,x+dx)),key=lambda pos:edges[pos])
            result[yy,xx]=ci
            changes.append({'color':ci,'reason':'接合仅以像素角相连的色块边缘','area_mm2':round(scale**2,4),
                            'bbox_mm':[5+(xx-.5)*scale,5+(yy-.5)*scale,5+(xx+.5)*scale,5+(yy+.5)*scale]})
    return result,changes


def separate_lobes(p, radius, line_width):
    if p.area<2 or p.interiors:return None
    # Start with ordinary fill-sized lobes, then test a finer opening for
    # small ornaments whose individual sections can take a short satin column.
    for reach, margin, min_area, retained in (
            (radius, line_width/2, .25, .60),
            (line_width, line_width/4, max(.14,3.5*line_width**2), .50)):
        opened=p.buffer(-reach,quad_segs=6).buffer(reach-margin,quad_segs=6).intersection(p)
        parts=[part for part in getattr(opened,'geoms',()) if part.geom_type=='Polygon' and part.area>=min_area]
        areas=sorted((part.area for part in parts),reverse=True)
        if len(parts)>=2 and areas[1]>=areas[0]*.22 and sum(areas)>=p.area*retained:
            return unary_union(parts)
    return None


def widen_internal_gaps(classes, scale, line_width, colors):
    """Give a narrow, already drawn separator room between two same-colour islands."""
    gap_px = 2.4 * line_width / scale
    changes = []
    if gap_px < 2:
        return classes, changes
    result = classes.copy()
    brush = np.ones((3, 3), np.uint8)
    # Do not move the silhouette or paint cloth. A separator takes the colour
    # that was actually drawn in the gap, including white.
    interior = cv2.erode((classes != 255).astype(np.uint8), brush, borderType=cv2.BORDER_CONSTANT,
                         borderValue=0).astype(bool)
    for ci in range(colors):
        _, components, stats, _ = cv2.connectedComponentsWithStats((classes == ci).astype(np.uint8))
        large = stats[:, cv2.CC_STAT_AREA] * scale**2 >= 4 * line_width**2
        large[0] = False
        if np.count_nonzero(large) < 2:
            continue
        space = (~large[components]).astype(np.uint8)
        distances, owners = cv2.distanceTransformWithLabels(space, cv2.DIST_L2, 5,
                                                           labelType=cv2.DIST_LABEL_CCOMP)
        # The boundary between two nearest-component labels bisects the gap.
        # An ordinary convex edge alone can never seed a separator.
        owners = owners.astype(np.float32)
        seam = (cv2.dilate(owners, brush) != cv2.erode(owners, brush))
        seam &= (classes != ci) & interior & (distances > 0) & (distances < gap_px / 2)
        if not seam.any():
            continue
        reach, seeds = cv2.distanceTransformWithLabels((~seam).astype(np.uint8), cv2.DIST_L2, 5,
                                                       labelType=cv2.DIST_LABEL_PIXEL)
        paint = np.full(int(seeds.max()) + 1, 255, np.uint8)
        paint[seeds[seam]] = classes[seam]
        trim = (classes == ci) & interior & (reach <= gap_px / 2)
        for label in np.unique(components[trim]):
            if not large[label]:
                continue
            x, y, w, h, area = map(int, stats[label])
            roi = pixel_window(classes.shape, x, y, w, h, 1)
            island = components[roi] == label
            remove = trim[roi] & island
            target = paint[seeds[roi]]
            for ink in np.unique(target[remove]):
                patch = remove & (target == ink)
                _, parts = cv2.connectedComponents(patch.astype(np.uint8))
                rim = cv2.dilate((classes[roi] == ink).astype(np.uint8), brush).astype(bool)
                attached = np.unique(parts[patch & rim])
                remove[patch & ~np.isin(parts, attached)] = False
            pixels = int(remove.sum())
            if not pixels or pixels > area * .18:
                continue
            rest = (island & ~remove).astype(np.uint8)
            # Preserve enclosed eyelets as well as the number of islands.
            # This also rejects a cut which opens a hole to the exterior.
            _, before = cv2.findContours(island.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
            _, after = cv2.findContours(rest, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
            if (after is None or before.shape!=after.shape
                    or np.count_nonzero(after[0,:,3] == -1) != np.count_nonzero(before[0,:,3] == -1)):
                continue
            result[roi][remove] = target[remove]
            changes.append({'color': ci, 'reason': '加宽相邻花纹间的原有分隔',
                            'area_mm2': round(pixels * scale**2, 3),
                            'bbox_mm': [5+x*scale, 5+y*scale, 5+(x+w-1)*scale, 5+(y+h-1)*scale]})
    return result, changes


def salvage_drawn_line(p, mask, local, ci, line, scale, origin):
    """Grow a physically thin but distinct, nearly straight original stroke.

    A single neighbouring solid ink must supply the clearance; never overwrite
    another feature or cloth. Limit normal displacement, not just area change.
    """
    if p.interiors or p.area < .12:return None
    axis,length,minor=main_axis(p)
    if not (max(1.,5*line)<=length and .33*line<=minor<=1.75*line and length>=4.2*minor and
            p.area>=.72*p.convex_hull.area):return None
    c=np.asarray(p.centroid.coords[0]);proj=(np.asarray(p.exterior.coords)-c)@axis
    diameter=max(line*1.25+scale*.7,minor)
    endpoints=[c+axis*(proj.min()+diameter/2), c+axis*(proj.max()-diameter/2)]
    path=LineString(endpoints).buffer(diameter/2,quad_segs=8)
    if p.hausdorff_distance(path)>line*.95:return None
    mark=polygon_mask(path,mask.shape,scale,origin)|mask
    guard=polygon_mask(path.buffer(line*.78),mask.shape,scale,origin)&~mask
    neighbours=np.unique(local[guard]);
    if len(neighbours)!=1 or int(neighbours[0]) in (ci,255) or not (mark&~mask).any():return None
    return mark


def exposed_hairline(p, mask, local, ci, line, scale, origin):
    """Give a straight, isolated stroke one printable thread of width.

    Only add cloth pixels inside the original image bounds. Exclude pixels
    touching other islands of this ink so a gap cannot become a connection.
    """
    if p.interiors or scale>line*.85 or p.area<.18:return None
    axis,length,minor=main_axis(p)
    if not (.40*line<=minor<line and length>=max(2.,8*minor) and p.area>=.90*length*minor):return None
    norm=np.array([-axis[1],axis[0]])
    c=np.asarray(p.centroid.coords[0]);ex=np.asarray(p.exterior.coords)-c
    start,end=(ex@axis).min(),(ex@axis).max()
    yy,xx=np.ogrid[:mask.shape[0],:mask.shape[1]]
    dx=5+(xx+origin[0])*scale-c[0];dy=5+(yy+origin[1])*scale-c[1]
    along=dx*axis[0]+dy*axis[1];perp=dx*norm[0]+dy*norm[1]
    diameter=max(line*1.08,scale*np.ceil(line/scale))
    # With an even number of requested pixels, keep the half-pixel bias
    # consistent along an axis-aligned stroke rather than rounding to one.
    offsets=(0,.48*scale,-.48*scale)
    other=cv2.dilate(((local==ci)&~mask).view(np.uint8),np.ones((3,3),np.uint8)).astype(bool)
    available=(local==255)&~other
    for off in offsets:
        added=(np.abs(perp-off)<=diameter/2)&(along>=start)&(along<=end)&available
        new_area=(int(mask.sum())+int(added.sum()))*scale**2
        if new_area >= .97*length*line and added.any():return mask|added
    return None


def space_parallel_inlays(classes, groups, scale, thread):
    """Separate repeated enclosed strokes without erasing any or painting cloth."""
    from studio_feasibility import detail_metrics
    line, _ = thread_detail_limits(thread)
    target = .7 if thread=='75D' else 1.
    output=classes.copy(); changes=[]
    for ci,parts in enumerate(groups):
        for p in parts:
            if not 2<=len(p.interiors)<=8 or p.area>90:continue
            shell=Polygon(p.exterior); holes=[Polygon(r) for r in p.interiors]
            axes=[main_axis(h) for h in holes]; a0=axes[0][0]
            if any(not (.70*target<=w<=1.25*target and 1.9*w<=l<=6*w and abs(a@a0)>.96 and
                        h.area>=.9*h.convex_hull.area) for h,(a,l,w) in zip(holes,axes)):continue
            centers=np.asarray([h.centroid.coords[0] for h in holes]); sizes=[h.area for h in holes]
            if max(sizes)>1.4*min(sizes) or detail_metrics(p)['narrow_fraction'][thread]<=.04:continue
            xy=np.rint((centers-5)/scale).astype(int); inks=output[xy[:,1],xy[:,0]]
            if inks[0] in (ci,255) or np.any(inks!=inks[0]):continue
            ink=int(inks[0]); norm=np.array([-a0[1],a0[0]]); middle=centers.mean(axis=0)
            x0,y0,x1,y1=np.rint((np.array(shell.bounds)-5)/scale).astype(int)
            roi=pixel_window(classes.shape,x0,y0,x1-x0+1,y1-y0+1,round(.22/scale)+2)
            current=output[roi];origin=(roi[1].start,roi[0].start)
            shell_mask=polygon_mask(shell,current.shape,scale,origin); inner=polygon_mask(shell.buffer(-1.6*scale),current.shape,scale,origin)
            actual_inks=np.unique(current[inner])
            if any(c not in (ci,ink) for c in actual_inks):continue
            # Only an adjacent solid background may receive a small expansion.
            surround=polygon_mask(shell.buffer(.20),current.shape,scale,origin)&~shell_mask
            background=set(current[surround].tolist())-{ci}; permit_grow=len(background)==1 and not ({255,ink}&background)
            best=None
            for factor in (1.16,1.24):
                for length_factor in (.94,.87,.81):
                    for diameter in (target+.07,target+.11):
                        new=[]
                        for (axis,length,_),c in zip(axes,centers):
                            c=c+(factor-1)*((c-middle)@norm)*norm; d=(length_factor*length-diameter)/2
                            new.append(LineString([c-d*axis,c+d*axis]).buffer(diameter/2,quad_segs=12))
                        shift=max(h.hausdorff_distance(q) for h,q in zip(holes,new))
                        if shift>1.9*line or any(abs(q.area/h.area-1)>.23 for h,q in zip(holes,new)):continue
                        ornaments=unary_union(new)
                        for grow in ((0,.14) if permit_grow else (0,)):
                            outer=shell.buffer(grow) if grow else shell
                            if not outer.contains(ornaments):continue
                            body=outer.difference(ornaments)
                            if body.geom_type!='Polygon' or len(body.interiors)!=len(holes):continue
                            if detail_metrics(body)['narrow_fraction'][thread]>.018:continue
                            cost=shift+.8*grow
                            if best is None or cost<best[0]:best=(cost,outer,ornaments)
            if best is None:continue
            mask=polygon_mask(best[1],current.shape,scale,origin); decorations=polygon_mask(best[2],current.shape,scale,origin)
            allowed=np.isin(current,[ci,ink,*background])&(current!=255)&mask
            old=current.copy();current[allowed]=ci;current[decorations&allowed]=ink
            changed=int(np.count_nonzero(current!=old))
            if changed:changes.append({'color':ink,'count':len(holes),'reason':'保留并排短纹，留开针路间隙','area_mm2':round(changed*scale**2,3),'bbox_mm':list(p.bounds)})
    return output,changes


def omitted_motifs(changes, line_width):
    """Find repeated, similarly sized original marks lost at finished size.

    Work on omission records only. Nearest-neighbour lookups are bounded even
    on noisy photos; dust is not evidence of a missing motif. Significant
    single ornaments also deserve review, without access to semantic labels.
    """
    groups = []
    for color in {c['color'] for c in changes if c['reason'].startswith('省略')}:
        items = [c for c in changes if c['reason'].startswith('省略') and c['color']==color
                 and c['area_mm2'] >= max(.09, 2.5*line_width**2)]
        if not items: continue
        boxes = np.asarray([c['bbox_mm'] for c in items]); sizes = boxes[:,2:]-boxes[:,:2]
        centres = (boxes[:,:2]+boxes[:,2:])/2; areas=np.asarray([c['area_mm2'] for c in items])
        lengths=sizes.max(axis=1); parents=list(range(len(items)))
        def root(i):
            while i!=parents[i]:
                parents[i]=parents[parents[i]];i=parents[i]
            return i
        tree=cKDTree(centres)
        for i in range(len(items)):
            if lengths[i]<3*line_width:continue
            _, near=tree.query(centres[i],k=min(12,len(items)),distance_upper_bound=max(1.5,1.4*lengths[i]))
            for j in np.atleast_1d(near):
                if j>=len(items) or i==j or max(areas[i],areas[j])>2.1*min(areas[i],areas[j]):continue
                if np.max(np.maximum(sizes[i],sizes[j])/np.maximum(.01,np.minimum(sizes[i],sizes[j])))>1.8:continue
                parents[root(i)]=root(int(j))
        clusters={}
        for i in range(len(items)):clusters.setdefault(root(i),[]).append(i)
        for ids in clusters.values():
            area=float(areas[ids].sum()); extent=boxes[ids]
            if not ((len(ids)>=3 and area>=max(.65,12*line_width**2)) or areas[ids].max()>=1.8):continue
            groups.append({'color':color,'count':len(ids),'area_mm2':round(area,3),
                           'bbox_mm':[float(x) for x in np.r_[extent[:,:2].min(axis=0),extent[:,2:].max(axis=0)]]})
    return sorted(groups,key=lambda g:-g['area_mm2'])


def unattended_fit_stop(report, aspect_ratio):
    """Stop *only* unattended export when the fitted art has already lost many landmarks.

    These are workload triage limits, not a way to turn rejected files into passes.
    Sparse changes still get the actual needle check; originals and manual export survive.
    """
    groups = (report or {}).get('omitted_motifs', [])
    count = sum(item['count'] for item in groups)
    if len(groups) < 6 or count < 24:
        return None
    width = report['width']
    frame_area = width**2 * max(.01, aspect_ratio)  # stored aspect = height / width
    lost_area = sum(item['area_mm2'] for item in groups)
    if lost_area < max(4.0, frame_area*.005):
        return None
    return {'groups':len(groups), 'parts':count, 'area_mm2':round(lost_area,2),
            'message':f'按 {width:g} mm / {report["thread"]} 适配后，已有 {len(groups)} 组、{count} 处明显花纹会消失。已在排针前停下；请从原图放大尺寸或简化花纹后重试。'}


def fit_artwork(source, destination, width, thread):
    source, destination = Path(source), Path(destination)
    meta, groups, _ = extract_geometries(source, width)
    original = np.load(source / 'classes.npy')
    result = original.copy()
    scale = width / max(1, meta['width_px'] - 1)
    # Conservative, adjustable trial presets, not universal machine limits.
    line_width, min_spot = thread_detail_limits(thread)
    radius = min_spot / 2
    kernel_size = max(3, 2 * round(.25 / scale) + 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    exterior = cv2.dilate((original == 255).astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    changes = []; decisions = []; kept_lines=[]
    all_removed = np.zeros(original.shape, bool)
    for ci, polygons in enumerate(groups):
        _, components, stats, _ = cv2.connectedComponentsWithStats((original == ci).astype(np.uint8), connectivity=8)
        seen = set()
        for p in polygons:
            x0,y0,x1,y1=np.rint((np.asarray(p.bounds)-5)/scale).astype(int)
            region=pixel_window(original.shape,x0,y0,x1-x0+1,y1-y0+1,1)
            mask=polygon_mask(p,components[region].shape,scale,(region[1].start,region[0].start))
            labels = components[region][mask]; labels = labels[labels > 0]
            if not len(labels):continue
            component = int(np.bincount(labels).argmax())
            if component in seen:
                continue
            seen.add(component)
            x,y,w,h=map(int,stats[component,:4])
            region=pixel_window(original.shape,x,y,w,h,kernel_size//2)
            origin=(region[1].start,region[0].start)
            mask = components[region] == component
            outside = bool((mask & exterior[region]).any())
            # Keep the main silhouette. Interior fragments can be removed;
            # isolated dust may disappear but meaningful exterior parts stay.
            if outside and p.area >= .12:
                if approximate_width(p)<line_width:
                    mark=exposed_hairline(p,mask,original[region],ci,line_width,scale,origin)
                    if mark is not None:kept_lines.append((region,np.packbits(mark),ci,p.bounds))
                continue
            if p.interiors:
                # Reassess a substantial base after its small inlays disappear.
                # Judging only material between those inlays can erase the base too.
                shell=Polygon(p.exterior)
                if (p.area>=.45*shell.area and not shell.buffer(-radius).is_empty
                        and all(Polygon(ring).area<min_spot**2*.55 for ring in p.interiors)):
                    continue
            target = None
            reason = None
            bounds = p.bounds
            short, long = sorted([bounds[2]-bounds[0], bounds[3]-bounds[1]])
            small_network = p.area < 10 and long / max(short, .001) < 3 and p.buffer(-radius).is_empty
            lobes=separate_lobes(p,radius,line_width)
            if lobes is not None:
                target=polygon_mask(lobes,mask.shape,scale,origin)&mask
                reason='分开挤在一起的花纹，保留主要形状'
            elif small_network or (p.area < 2 and p.buffer(-radius).is_empty) or (p.area < 10 and approximate_width(p) < line_width):
                detail = fine_line(p)
                if detail is not None and detail[1] >= line_width * .75:
                    continue
                if p.area>=1.5:
                    core=p.buffer(-line_width,quad_segs=6)
                    if not core.is_empty and core.buffer(line_width,quad_segs=6).intersection(p).area>=.65*p.area:
                        continue
                if not p.interiors and p.area>=.22 and p.area>=.88*p.convex_hull.area:
                    _,length,minor=main_axis(p)
                    if length>=max(.85,4*line_width) and minor>=1.65*line_width and satin_candidate(p)[0]:
                        continue
                target = np.zeros_like(mask)
                reason = '省略过小或过细的局部'
            if target is None:
                continue
            if reason.startswith('省略'):
                mark=salvage_drawn_line(p,mask,original[region],ci,line_width,scale,origin)
                if mark is not None:
                    kept_lines.append((region,np.packbits(mark),ci,p.bounds))
                    continue
            removed = mask & ~target
            if not removed.any():
                continue
            # Keep the same per-pixel decisions, packed to one bit per pixel.
            # Hundreds of full-size boolean masks otherwise retain hundreds of MB.
            all_removed[region] |= removed
            decisions.append((ci, p, region, np.packbits(mask), np.packbits(removed), reason))
    # Decide all omissions before choosing replacement colours. Otherwise a
    # deleted ornament's holes can fill with its colour and resurrect fragments.
    stable = (original != 255) & ~all_removed
    nearest = None
    for ci, p, region, mask_bits, removed_bits, reason in decisions:
        local=original[region]
        mask = np.unpackbits(mask_bits, count=local.size).view(np.bool_).reshape(local.shape)
        removed = np.unpackbits(removed_bits, count=local.size).view(np.bool_).reshape(local.shape)
        rim = cv2.dilate(mask.astype(np.uint8), kernel).astype(bool) & ~mask
        neighbours = local[rim & stable[region] & (local != ci)]
        if not neighbours.size and stable.any():
            if nearest is None:
                nearest=distance_transform_edt(~stable,return_distances=False,return_indices=True)
            neighbours = original[tuple(nearest[:,region[0],region[1]][:,removed])]
            neighbours = neighbours[neighbours != ci]
        replacement = int(np.bincount(neighbours, minlength=len(meta['colors'])).argmax()) if neighbours.size else 255
        if replacement == 255 and p.area >= .12:
            continue
        result[region][removed] = replacement
        changes.append({'color': ci, 'reason': reason, 'area_mm2': round(float(removed.sum() * scale**2), 3),
                        'bbox_mm': list(p.bounds), 'replacement_color': replacement if replacement != 255 else None})
    for region,bits,ci,bounds in kept_lines:
        tile=result[region]; mark=np.unpackbits(bits,count=tile.size).view(np.bool_).reshape(tile.shape)
        changed=int(np.count_nonzero(mark & (tile!=ci)))
        tile[mark]=ci
        changes.append({'color':ci,'reason':'保留细线并微调线宽','area_mm2':round(changed*scale**2,3),'bbox_mm':list(bounds)})
    result, gap_changes = widen_internal_gaps(result, scale, line_width, len(meta['colors']))
    changes.extend(gap_changes)
    result, inlay_changes = space_parallel_inlays(result, groups, scale, thread)
    changes.extend(inlay_changes)
    result, tip_changes=join_pixel_tips(result,scale,line_width,len(meta['colors']))
    changes.extend(tip_changes)
    # Compact empty palette entries without reclustering surviving colours.
    colors = []
    classes = np.full(result.shape, 255, np.uint8)
    for old, color in enumerate(meta['colors']):
        mask = result == old
        if not mask.any():
            continue
        new = len(colors); classes[mask] = new
        entry = copy.deepcopy(color); entry.update(key=f'color-{new+1}', pixel_count=int(mask.sum()))
        colors.append(entry)
    unchanged = np.array_equal(result, original)
    if unchanged:
        classes, colors = original, meta['colors']
    if not colors:
        raise ValueError('当前尺寸下没有足够清楚的主体，请增大尺寸后重试。原图已保留。')
    destination.mkdir(parents=True, exist_ok=True)
    rgba = np.zeros((*classes.shape, 4), np.uint8)
    for ci, color in enumerate(colors):
        rgba[classes == ci] = [*color['rgb'], 255]
    report = {'policy': POLICY, 'width': width, 'thread': thread, 'min_spot_mm': min_spot,
              'reference_width_mm': line_width, 'changes': changes,
              'omitted_motifs': omitted_motifs(changes, line_width),
              'strengthened_lines':len(kept_lines),
              'removed_parts': sum(c['reason'].startswith('省略') for c in changes),
              'separated_motifs': sum(c['reason'].startswith('分开') for c in changes),
              'gap_adjusted_parts': len(gap_changes), 'retained_parallel_marks':sum(c['count'] for c in inlay_changes),
              'changed_area_mm2': round(float(np.count_nonzero(original != result) * scale**2), 3),
              'physically_sewn': False}
    meta.update(colors=colors, source='size_fit', fit_report=report)
    # Preserve the source's tracing policy, including smooth legacy outlines.
    Image.fromarray(rgba).save(destination / 'flat.png')
    if not unchanged:
        delta = (result != original).astype(np.uint8)
        halo = max(1, min(6, round(.10 / scale)))
        stamp = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2*halo+1, 2*halo+1))
        marked = rgba.copy()
        marked[cv2.dilate(delta, stamp) != 0] = [255, 255, 255, 255]
        marked[delta != 0] = [178, 22, 105, 255]
        Image.fromarray(marked).save(destination / 'fit-changes.png')
    np.save(destination / 'classes.npy', classes)
    save_json(destination / 'palette.json', meta)
    save_json(destination / 'fit-report.json', report)
    view = artwork_view(destination, width, thread)
    write_svg(view, destination / 'layers.svg'); save_json(destination / 'preview.json', view)
    return meta, view, report

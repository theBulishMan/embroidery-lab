"""Assign source pixels to thread colours with bounded working buffers."""
import cv2
import numpy as np
from skimage.color import deltaE_ciede2000, lab2rgb, rgb2lab


def stripes(shape):
    rows = max(1, 60000 // shape[1])
    for start in range(0, shape[0], rows):
        yield slice(start, start + rows)


def lab(rgb):
    return rgb2lab(rgb.astype(np.float32) / 255)


def auxiliary_background(rgb, color):
    target = lab(np.array(color, dtype=np.uint8))
    candidate = np.empty(rgb.shape[:2], dtype=np.uint8)
    for rows in stripes(candidate.shape):
        block = lab(rgb[rows])
        candidate[rows] = deltaE_ciede2000(block, np.broadcast_to(target, block.shape)) < 13
    return candidate


def packed_rgb(rgb):
    r, g, b = rgb.transpose(2, 0, 1)
    return (r.astype(np.uint32) << 16) | (g.astype(np.uint32) << 8) | b


def flat_palette(rgb, mask, limit):
    colors = np.empty(0, dtype=np.uint32)
    for rows in stripes(mask.shape):
        keys = packed_rgb(rgb[rows])[mask[rows]]
        colors = np.union1d(colors, np.unique(keys))
        if len(colors) > limit:
            return None
    classes = np.full(mask.shape, 255, np.uint8)
    for rows in stripes(mask.shape):
        visible = mask[rows]
        classes[rows][visible] = np.searchsorted(colors, packed_rgb(rgb[rows])[visible])
    palette = ((colors[:, None] >> [16, 8, 0]) & 255).astype(np.uint8)
    return classes, palette, lab(palette)


def sample_colors(rgb, mask):
    # Sample by foreground pixel rank so transparent margins cannot bias the palette.
    cumulative = np.cumsum(mask.sum(axis=1))
    ranks = np.linspace(0, cumulative[-1] - 1, min(35000, cumulative[-1])).astype(np.intp)
    ys = np.searchsorted(cumulative, ranks, side='right')
    sample = np.empty((len(ranks), 3), dtype=np.uint8)
    for rows in stripes(mask.shape):
        start, stop = np.searchsorted(ys, [rows.start, rows.stop])
        if start < stop:
            offset = cumulative[rows.start - 1] if rows.start else 0
            sample[start:stop] = rgb[rows][mask[rows]][ranks[start:stop] - offset]
    return lab(sample)


def resolve_composited_edges(rgb, unknown, owners, seeds, boundary, palette):
    """Resolve occluded underpaint only along evidenced ink or alpha boundaries."""
    y, x = np.nonzero(unknown)
    if not len(y):
        return
    local = owners[y, x]
    outer = boundary[y, x] if boundary is not None else np.zeros(len(y), bool)
    junction = (local & (local - 1)) != 0
    eligible = (local != 0) & (outer | junction)
    if not eligible.any():
        return
    y, x, local, outer = y[eligible], x[eligible], local[eligible], outer[eligible]
    samples = rgb[y, x].astype(np.float32)
    # The intermediate colour can come from a broad underlying band whose
    # nearest solid pixel is several pixels away from its antialiased tip.
    radius = min(32, max(6, round(max(unknown.shape) * .065)))
    window = np.ones((2 * radius + 1, 2 * radius + 1), np.uint8)
    vicinity = np.zeros(len(y), np.uint16)
    for i in range(len(palette)):
        vicinity |= cv2.dilate((seeds == i).view(np.uint8), window)[y, x].astype(np.uint16) << i
    resolved = np.zeros(len(y), bool)
    for i, a in enumerate(palette):
        for j in range(i):
            b = palette[j] - a
            pair = (1 << i) | (1 << j)
            candidate = ~resolved & ((local & pair) != 0) & (outer | ((vicinity & pair) == pair))
            diff = samples[candidate] - a
            t = ((diff @ b) / max(1., float(b @ b))).clip(0, 1)
            resolved[candidate] |= np.abs(diff - t[:, None] * b).max(axis=1) <= 5
            for k in range(j):
                trio = pair | (1 << k)
                adjacent = local & trio
                candidate = ~resolved & ((adjacent & (adjacent - 1)) != 0) & ((vicinity & trio) == trio)
                if not candidate.any():
                    continue
                basis = np.stack((b, palette[k] - a))
                if np.linalg.matrix_rank(basis) < 2:
                    continue
                diff = samples[candidate] - a
                weights = diff @ np.linalg.pinv(basis)
                inside = (weights.min(axis=1) >= -.015) & (weights.sum(axis=1) <= 1.015)
                resolved[candidate] |= inside & (np.abs(diff - weights @ basis).max(axis=1) <= 5)
    unknown[y[resolved], x[resolved]] = 0


def coherent_stroke_colors(rgb, residual, owners, boundary, alpha, palette, slots, exact_palette):
    """Find a solid thin mark repeated on a single field, away from its fringe."""
    if slots <= 0:
        return []
    interior = residual.astype(bool) & (owners != 0) & ((owners & (owners - 1)) == 0)
    if boundary is not None:
        interior &= ~boundary & (alpha >= 250)
    y, x = np.nonzero(interior)
    if len(x) < 5:
        return []
    pixels = rgb[y, x].astype(np.uint32)
    keys = (pixels[:, 0] << 16) | (pixels[:, 1] << 8) | pixels[:, 2]
    values, counts = np.unique(keys, return_counts=True)
    frequent = counts >= 5
    if frequent.sum() > 64:
        return []
    found = []
    for value in values[frequent]:
        color = ((int(value) >> np.array([16, 8, 0])) & 255).astype(np.int16)
        if not exact_palette and np.abs(palette.astype(np.int16) - color).max(axis=1).min() < 18:
            continue
        hit = keys == value
        xx, yy = x[hit], y[hit]
        component = np.zeros((np.ptp(yy)+1, np.ptp(xx)+1), np.uint8)
        component[yy-yy.min(), xx-xx.min()] = 1
        _, _, stats, _ = cv2.connectedComponentsWithStats(component)
        if np.any((stats[1:, 4] >= 5) & (stats[1:, 2:4].max(axis=1) >= 5)):
            found.append(int(value))
            if len(found) > slots:
                return []
    return found


def antialiased_inks(rgb, mask, limit, alpha=None, *, require_all=False, _stroke_inks=()):
    """Recover the actual solid inks of a rasterized drawing.

    The core of a digital ink has a constant colour in a cross of five
    pixels. A real one-pixel stroke need not have a core: do not absorb it
    unless the colour is demonstrably a mixture of *local* adjacent inks.
    Photos, gradients and ambiguous strokes fall back to full quantization.
    """
    h,w=mask.shape
    if min(h,w)<5:return None
    kernel=cv2.getStructuringElement(cv2.MORPH_CROSS,(3,3))
    cores=cv2.erode(mask.view(np.uint8),kernel,borderType=cv2.BORDER_CONSTANT,borderValue=0).astype(bool)
    for ch in range(3):
        plane=np.ascontiguousarray(rgb[:,:,ch])
        cores &= cv2.erode(plane,kernel)==cv2.dilate(plane,kernel)
    if np.count_nonzero(cores)<.75*np.count_nonzero(mask):return None
    counts={}
    for rows in stripes(mask.shape):
        keys,n=np.unique(packed_rgb(rgb[rows])[cores[rows]],return_counts=True)
        if len(keys)>160:return None
        for key,count in zip(keys.tolist(),n.tolist()):counts[key]=counts.get(key,0)+count
        if len(counts)>160:return None
    solid=np.array(sorted({k for k,n in counts.items() if n>=3}.union(_stroke_inks)),dtype=np.uint32)
    if not 1<=len(solid)<=limit:return None
    pal=((solid[:,None]>>[16,8,0])&255).astype(np.uint8)
    classes=np.full((h,w),255,np.uint8)
    for rows in stripes(mask.shape):
        v=mask[rows];values=packed_rgb(rgb[rows])[v]
        ix=np.searchsorted(solid,values).clip(0,len(solid)-1)
        classes[rows][v]=np.where(solid[ix]==values,ix,255)
    unknown=mask&(classes==255)
    if not unknown.any():return classes,pal,lab(pal)
    # For each transition, find the source inks within 3 pixels.
    # Blending in encoded space or linear light can move the values along
    # slightly different curves. Isolated unfamiliar ink remains unexplained.
    owners=np.zeros((h,w),np.uint16)
    for i in range(len(solid)):
        nearby=cv2.dilate((classes==i).view(np.uint8),np.ones((7,7),np.uint8))
        owners[nearby!=0]|=1<<i
    seeds=classes.copy()
    colors=pal.astype(np.float32); centers=lab(pal)
    unexplained=np.zeros(mask.shape,np.uint8)
    boundary=mask&~cv2.erode(mask.view(np.uint8),np.ones((5,5),np.uint8),borderType=cv2.BORDER_CONSTANT,borderValue=0).astype(bool) if alpha is not None else None
    for rows in stripes(mask.shape):
        sel=unknown[rows]
        if not sel.any():continue
        data=rgb[rows][sel].astype(np.float32); near=owners[rows][sel]
        # Allow a couple of colour levels of raster rounding near a pure field.
        reliable=np.zeros(len(data),bool)
        touching_inks=(near&(near-1))!=0
        rounding=touching_inks.copy() if require_all else np.ones(len(data),bool)
        if boundary is not None:
            rounding |= boundary[rows][sel]
        for i,c in enumerate(colors):
            present=(near&(1<<i))!=0; reliable |= present&rounding&(np.max(np.abs(data-c),axis=1)<=6)
            for j in range(i):
                both=present&((near&(1<<j))!=0)&~reliable
                if not both.any():continue
                vec=colors[j]-c; u=((data[both]-c)@vec/max(1,float(vec@vec))).clip(0,1)
                reliable[both]=np.max(np.abs(data[both]-c-u[:,None]*vec),axis=1)<=16
        # Some rasterizers blend subpixel edges over an obscured shape before
        # writing alpha. Only near a transparent edge, for a partially covered
        # pixel, accept a nearby shade of the same *local* solid ink.
        lab_data=lab(rgb[rows][sel]);best=np.full(len(data),np.inf,np.float32)
        picked=np.zeros(len(data),np.uint8)
        for i,c in enumerate(centers):
            delta=((lab_data-c)**2).sum(axis=1)
            eligible=((near&(1<<i))!=0)&(delta<best)
            best[eligible]=delta[eligible];picked[eligible]=i
        if alpha is not None:
            reliable|=(boundary[rows][sel]|touching_inks)&(alpha[rows][sel]<255)&(best<=36**2)
        unexplained[rows][sel]=~reliable
        classes[rows][sel]=picked
    if unexplained.any():
        if unexplained.sum() <= max(24, .035 * mask.sum()):
            resolve_composited_edges(rgb, unexplained, owners, seeds, boundary, colors)
            if not _stroke_inks:
                strokes = coherent_stroke_colors(rgb, unexplained, owners, boundary, alpha, pal, limit-len(pal), require_all)
                if strokes:
                    return antialiased_inks(rgb, mask, limit, alpha, require_all=require_all, _stroke_inks=strokes)
        if require_all and unexplained.any():
            return None
        _,_,stats,_=cv2.connectedComponentsWithStats(unexplained)
        # Small colour rounding at an occasional three-ink junction is OK.
        if (unexplained.sum()>max(4,.001*mask.sum()) or
                np.any(stats[1:,cv2.CC_STAT_AREA]>=4) or np.any(stats[1:,2:4].max(axis=1)>=4)):return None
    return classes,pal,centers


def compressed_inks(rgb, mask, limit, white_matte):
    """Recover solid illustration inks from JPEG/WebP compression.

    Require broad, near-constant fields. Restrict each mixed pixel to nearby
    inks. A long unexplained mark on one field rejects this shortcut; it must
    go through full colour separation with its own candidate ink.
    """
    if min(mask.shape)<9:return None
    kernel=np.ones((5,5),np.uint8)
    interior=cv2.erode(mask.view(np.uint8),kernel,borderType=cv2.BORDER_CONSTANT,borderValue=0).astype(bool)
    spread=cv2.dilate(rgb,kernel)-cv2.erode(rgb,kernel)
    stable=interior&(spread.max(2)<=10)
    if stable.sum()<.56*mask.sum():return None
    counts={}
    for rows in stripes(mask.shape):
        keys,n=np.unique(packed_rgb(rgb[rows])[stable[rows]],return_counts=True)
        if len(keys)>1600:return None
        for k,v in zip(keys.tolist(),n.tolist()):counts[k]=counts.get(k,0)+v
        if len(counts)>3200:return None
    inks=[];minimum=max(8,int(stable.sum()*.00024))
    for k,n in sorted(counts.items(),key=lambda item:-item[1]):
        if n<minimum:break
        c=np.array([(k>>16)&255,(k>>8)&255,k&255],np.int16)
        # Small neutral differences can denote a white highlight against a
        # light grey panel. JPEG chroma variation around other inks is wider.
        tolerance=7 if c.min()>=236 and c.max()-c.min()<18 else 15
        if any(np.abs(c-ink).max()<=tolerance for ink in inks):continue
        inks.append(c)
        if len(inks)>limit:return None
    if not inks:return None
    if white_matte and not any(ink.min()>=248 for ink in inks):
        # A white highlight enclosed by other inks can be too thin for a
        # constant 5x5 JPEG tile. Keep its measured white core. Only the
        # separately identified outer matte may turn into exposed cloth.
        white=interior&(rgb.min(2)>=245)&((rgb.max(2)-rgb.min(2))<=12)
        core=cv2.erode(white.view(np.uint8),np.ones((3,3),np.uint8)).astype(bool)
        if int(core.sum())>=3:
            if len(inks)==limit:return None
            inks.append(np.median(rgb[core],axis=0).astype(np.int16))
    palette=np.array(inks,np.uint8);centers=lab(palette)
    seeds=np.zeros(mask.shape,np.uint16)
    halo=np.ones((9,9),np.uint8)
    for i,c in enumerate(inks):
        close=cv2.inRange(rgb,np.maximum(0,c-9).astype(np.uint8),np.minimum(c+9,255).astype(np.uint8))
        if white_matte and c.min()>=236:
            # Outer white is not a seed for an enclosed highlight. It has
            # its own matte candidate, even with a one-level JPEG colour cast.
            core=cv2.erode((interior&(close!=0)).view(np.uint8),np.ones((3,3),np.uint8),
                           borderType=cv2.BORDER_CONSTANT,borderValue=0).astype(bool)
            _,islands=cv2.connectedComponents((mask&(close!=0)).view(np.uint8))
            anchored=np.unique(islands[core]);anchored=anchored[anchored!=0]
            close=np.isin(islands,anchored).view(np.uint8)
        seeds[cv2.dilate(close,halo)!=0]|=1<<i
    if not white_matte and mask.all():
        near_white=rgb.min(2)>=242
        rim=np.r_[near_white[0],near_white[-1],near_white[:,0],near_white[:,-1]]
        paper=next((i for i,c in enumerate(inks) if c.min()>=248),None)
        if (paper is not None and rim.mean()>.55 and near_white[np.ix_([0,-1],[0,-1])].all()):
            # A JPEG block at the image limit can lose the white side of
            # an antialias transition. White is a real retained thread here.
            # Permit it only on the two outside pixel rows of this white sheet.
            for strip in (seeds[:2],seeds[-2:],seeds[:,:2],seeds[:,-2:]):
                strip|=1<<paper
    matte_index=len(inks)
    if white_matte:
        edge=~cv2.erode(mask.view(np.uint8),halo,borderType=cv2.BORDER_CONSTANT,borderValue=0).astype(bool)
        seeds[edge]|=1<<matte_index
    candidates=np.vstack((centers,lab(np.array([[255,255,255]],np.uint8)))) if white_matte else centers
    labels=np.full(mask.shape,255,np.uint8);unexplained=np.zeros(mask.shape,np.uint8)
    for rows in stripes(mask.shape):
        m=mask[rows]
        if not m.any():continue
        data=lab(rgb[rows][m]);owners=seeds[rows][m]
        if np.any(owners==0):return None
        single=(owners&(owners-1))==0
        best=np.full(len(data),np.inf,np.float32);choice=np.full(len(data),255,np.uint8)
        for i,c in enumerate(candidates):
            delta=((data-c)**2).sum(1);near=((owners&(1<<i))!=0)&((delta<=best) if i==matte_index else (delta<best))
            choice[near]=i;best[near]=delta[near]
        unexplained[rows][m]=(single&(best>19**2))|(best>55**2)
        if white_matte:choice[choice==matte_index]=255
        labels[rows][m]=choice
    if unexplained.any():
        _,_,stats,_=cv2.connectedComponentsWithStats(unexplained)
        if (unexplained.sum()>max(5,.002*mask.sum()) or np.any(stats[1:,4]>=8)
                or np.any(stats[1:,2:4].max(1)>=6)):return None
    return labels,palette,centers


def separate_colors(rgb, mask, limit, detailed, alpha=None, compressed=False, white_matte=False):
    exact = flat_palette(rgb, mask, limit)
    if exact is not None:
        if len(exact[1]) > 2:
            reduced = antialiased_inks(rgb, mask, limit, alpha, require_all=True)
            if reduced is not None and len(reduced[1]) < len(exact[1]):
                return (*reduced, 'solid-inks')
        return (*exact, 'exact')
    inks=antialiased_inks(rgb,mask,limit,alpha)
    if inks is not None:return (*inks,'solid-inks')
    if compressed and alpha is None:
        inks=compressed_inks(rgb,mask,limit,white_matte)
        if inks is not None:return (*inks,'compressed-inks')
    sample = sample_colors(rgb, mask)
    cv2.setRNGSeed(17)
    count = min(limit, len(np.unique(np.round(sample, 1), axis=0)))
    stop = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 80, .1)
    _, assigned, centers = cv2.kmeans(sample, count, None, stop, 3, cv2.KMEANS_PP_CENTERS)
    weights = np.bincount(assigned.ravel(), minlength=count)
    while len(centers) > 1:
        distances = deltaE_ciede2000(centers[:, None], centers[None, :])
        np.fill_diagonal(distances, np.inf)
        i, j = np.unravel_index(distances.argmin(), distances.shape)
        threshold = 10 if not detailed or max(centers[i, 0], centers[j, 0]) < 28 else 3
        if distances[i, j] >= threshold:
            break
        centers[i] = (centers[i] * weights[i] + centers[j] * weights[j]) / (weights[i] + weights[j])
        weights[i] += weights[j]
        centers = np.delete(centers, j, axis=0)
        weights = np.delete(weights, j)
    classes = np.full(mask.shape, 255, np.uint8)
    for rows in stripes(mask.shape):
        visible = mask[rows]
        if visible.any():
            block = lab(rgb[rows][visible])
            _, labels = cv2.batchDistance(block, centers, cv2.CV_32F, normType=cv2.NORM_L2SQR, K=1)
            classes[rows][visible] = labels.ravel()
    palette = np.clip(np.rint(lab2rgb(centers) * 255), 0, 255).astype(np.uint8)
    return classes, palette, centers, 'quantized'

"""Deterministic color limits and vector geometry after the AI artwork stage."""
from pathlib import Path
import json, colorsys, re,copy
from functools import lru_cache
import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError
from scipy.ndimage import distance_transform_edt, gaussian_filter1d
from shapely.geometry import Polygon
from shapely.affinity import affine_transform
from skimage.color import rgb2lab, deltaE_ciede2000
from skimage.measure import find_contours
from shapely.strtree import STRtree
from lxml import etree as ET
from studio_runtime import APP, PALETTE
from studio_separation import auxiliary_background, separate_colors

SVG='http://www.w3.org/2000/svg'
INK='http://www.inkscape.org/namespaces/inkscape'
STITCH='http://inkstitch.org/namespace'
Image.MAX_IMAGE_PIXELS=25_000_000

def save_json(path, value):
    path=Path(path);tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,separators=(',',':')));tmp.replace(path)

def normalize_image(source, destination, max_side=1800):
    try:
        with Image.open(source) as opened:
            # Some phone JPEGs contain auxiliary MPO images. Use the primary
            # photograph instead of rejecting a normal .jpg upload.
            if opened.format not in ['PNG','JPEG','MPO','WEBP']:
                raise ValueError('请上传PNG、JPG或WebP图片。')
            opened.seek(0)
            if opened.width*opened.height>25_000_000:
                raise ValueError('图片过大，请使用2500万像素以内的图片。')
            im=ImageOps.exif_transpose(opened).convert('RGBA')
            if max_side:im.thumbnail((max_side,max_side),Image.Resampling.LANCZOS)
            im.save(destination,'PNG')
    except (UnidentifiedImageError, OSError) as error:
        raise ValueError('图片无法读取，请重新选择PNG、JPG或WebP图片。') from error
    except Image.DecompressionBombError as error:
        raise ValueError('图片过大，请使用2500万像素以内的图片。') from error


def has_continuous_tones(source):
    """Choose the photo brief only with evidence of shading across the subject."""
    with Image.open(source) as opened:
        opened.thumbnail((144, 144))
        image = np.asarray(opened.convert('RGBA'))
    rgb = image[:, :, :3].astype(np.int16)
    visible = (image[:, :, 3] > 200) & (rgb.min(axis=2) < 241)
    if visible.sum() < 128:
        return False
    cells = rgb[visible].astype(np.int32) // 16
    histogram = np.bincount(cells @ [256, 16, 1], minlength=4096)
    common = np.partition(histogram, -18)[-18:].sum() / visible.sum()
    interior = visible[:-1, :-1] & visible[1:, :-1] & visible[:-1, 1:]
    if not interior.any():
        return False
    centre = rgb[:-1, :-1]
    variation = np.maximum(np.abs(centre-rgb[1:, :-1]), np.abs(centre-rgb[:-1, 1:])).max(axis=2)
    return bool(common < .72 and (variation[interior] <= 6).mean() < .58)

def color_name(rgb):
    r,g,b=[v/255 for v in rgb];h,l,s=colorsys.rgb_to_hls(r,g,b)
    if l<.21:return '黑色'
    if l>.91:return '白色' if max(r,g,b)-min(r,g,b)<.035 else '米白色'
    if l>.84 and max(r,g,b)-min(r,g,b)<.10:return '米白色'
    if s<.13:return '白色' if l>.82 else '灰色'
    h*=360
    if l<.46 and (h<65 or h>345):return '棕色'
    name='红色' if h<15 or h>=345 else '橙色' if h<45 else '黄色' if h<70 else '绿色' if h<165 else '青色' if h<195 else '蓝色' if h<255 else '紫色' if h<305 else '粉色'
    if name=='红色' and l>.65:name='粉色'
    return name

def match_threads(colors):
    entries=[]
    for line in PALETTE.read_text().splitlines():
        m=re.match(r'^\s*(\d+)\s+(\d+)\s+(\d+)\s+(.+?)\s+(\S+)\s*$',line)
        if m:entries.append({'rgb':list(map(int,m.group(1,2,3))),'name':m.group(4),'code':m.group(5)})
    labs=rgb2lab(np.array([e['rgb'] for e in entries])/255)
    for color in colors:
        target=rgb2lab(np.array(color['rgb'])/255)
        d=deltaE_ciede2000(labs,np.broadcast_to(target,labs.shape));i=int(d.argmin())
        color['thread']={**entries[i],'hex':'#'+''.join(f'{v:02X}' for v in entries[i]['rgb']),'delta_e_2000':round(float(d[i]),2)}
        color['brand']='Madeira Rayon（示范色卡）'

def recolor_artwork(source_folder, out, hex_colors, width=60, thread='105D'):
    """Replace palette entries without resampling or redrawing any pixel.

    Equal destination colors share one layer. The parent artwork and its
    existing stitch plans are never changed.
    """
    source_folder=Path(source_folder);out=Path(out)
    meta=json.loads((source_folder/'palette.json').read_text())
    if not isinstance(hex_colors,list) or len(hex_colors)!=len(meta['colors']):
        raise ValueError('配色数量与当前平面稿不一致，请重新载入。')
    if any(not isinstance(h,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',h) for h in hex_colors):
        raise ValueError('请选择有效颜色，或输入六位颜色值，例如 #F3D978。')
    old=np.load(source_folder/'classes.npy')
    classes=np.full(old.shape,255,np.uint8);colors=[];indices={}
    for previous,h in enumerate(hex_colors):
        h=h.upper()
        if h not in indices:
            ci=len(colors);indices[h]=ci;rgb=[int(h[n:n+2],16) for n in [1,3,5]]
            colors.append({'key':f'color-{ci+1}','name':color_name(rgb),'hex':h,'rgb':rgb,'pixel_count':0})
        ci=indices[h];mask=old==previous;classes[mask]=ci;colors[ci]['pixel_count']+=int(mask.sum())
    match_threads(colors)
    rgba=np.zeros((*old.shape,4),np.uint8)
    for ci,color in enumerate(colors):rgba[classes==ci]=[*color['rgb'],255]
    out.mkdir(parents=True,exist_ok=True)
    Image.fromarray(rgba).save(out/'flat.png');np.save(out/'classes.npy',classes)
    meta['colors']=colors;meta['source']='palette_edit';save_json(out/'palette.json',meta)
    view=artwork_view(out,width,thread);write_svg(view,out/'layers.svg');save_json(out/'preview.json',view)
    return meta,view

def flatten(source, out, max_colors, background_color=None, preserve_white=False, detail_mode='simplify', width=60, thread='105D', compressed_source=False, register_to=None):
    if detail_mode not in {'preserve','simplify'}:raise ValueError('细节处理方式无效。')
    detailed=detail_mode=='preserve'
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    with Image.open(source) as opened:
        compressed=compressed_source or opened.format in ('JPEG','MPO','WEBP')
        a=np.array(opened.convert('RGBA'))
    # Generated alpha is preferred. For an opaque white canvas, remove only
    # white pixels connected to the edge, retaining enclosed white features.
    has_alpha=np.mean(a[:,:,3]<100)>.01
    if has_alpha:
        mask=a[:,:,3]>200
    elif background_color:
        # Only remove the known auxiliary canvas color connected to an edge.
        # White heads, white borders and highlights are normal artwork colors.
        rgb=np.array([int(background_color[i:i+2],16) for i in [1,3,5]])
        candidate=auxiliary_background(a[:,:,:3],rgb)
        _,lab=cv2.connectedComponents(candidate)
        edge=np.unique(np.concatenate([lab[0],lab[-1],lab[:,0],lab[:,-1]]));edge=edge[edge!=0]
        bg=np.isin(lab,edge)
        if bg.mean()<.01:raise ValueError('AI没有输出可分离的辅助背景，原始结果已保留，请重试；没有自动删除白色。')
        mask=~bg
    elif preserve_white:
        # An opaque white canvas and an open white subject cannot be
        # distinguished by a white threshold. Preserve, unless the user opts in.
        mask=a[:,:,3]>200
    else:
        white=(a[:,:,:3].min(2)>237)&(a[:,:,:3].max(2)-a[:,:,:3].min(2)<16)
        n,lab,_,_=cv2.connectedComponentsWithStats(white.astype(np.uint8))
        edge=np.unique(np.concatenate([lab[0],lab[-1],lab[:,0],lab[:,-1]]));edge=edge[edge!=0]
        bg=np.isin(lab,edge)
        if bg.mean()<.04:raise ValueError('生成图没有清晰的独立主体，请重试并描述一个背景简单的图案。')
        mask=~bg
    n,labels,stats,_=cv2.connectedComponentsWithStats(mask.astype(np.uint8))
    keep=[i for i in range(1,n) if stats[i,cv2.CC_STAT_AREA]>=(2 if detailed else 16)]
    mask=np.isin(labels,keep)
    if mask.sum()<200:raise ValueError('没有识别到可制版的主体，请换图或补充描述。')
    x,y,w,h=cv2.boundingRect(mask.view(np.uint8));bbox=[x,y,x+w,y+h]
    a=a[bbox[1]:bbox[3],bbox[0]:bbox[2]];mask=mask[bbox[1]:bbox[3],bbox[0]:bbox[2]]
    classes,rgbs,centers,separation=separate_colors(a[:,:,:3],mask,max_colors,detailed,a[:,:,3] if has_alpha else None,
                                                 compressed=compressed,white_matte=not (background_color or preserve_white))
    if separation=='compressed-inks' and (mask&(classes==255)).any():
        mask=mask&(classes!=255)
        x,y,w,h=cv2.boundingRect(mask.view(np.uint8))
        if w<1 or h<1:raise ValueError('没有识别到可制版的主体。')
        classes=classes[y:y+h,x:x+w];mask=mask[y:y+h,x:x+w]
        bbox=[bbox[0]+x,bbox[1]+y,bbox[0]+x+w,bbox[1]+y+h]
    registration=None
    if register_to is not None:
        reference=np.load(Path(register_to)/'classes.npy',mmap_mode='r',allow_pickle=False)
        rh,rw=reference.shape
        if min(rh,rw,h,w)>1:
            drift=abs((h-1)/(w-1)/((rh-1)/(rw-1))-1)
            def thumbnail(m):
                return cv2.resize(m.astype(np.uint8),(160,160),interpolation=cv2.INTER_AREA).astype(bool)
            old=thumbnail(reference!=255);new=thumbnail(mask)
            overlap=np.count_nonzero(old&new)/max(1,np.count_nonzero(old|new))
            registration={'height_change_percent':round(100*drift,2),'silhouette_overlap_percent':round(100*overlap,2),
                          'needs_review':bool(drift>.12 or overlap<.82)}
            if not registration['needs_review']:
                # Match the source grid so the machine file retains both
                # measured dimensions. Keep thread indices discrete.
                classes=cv2.resize(classes,(rw,rh),interpolation=cv2.INTER_NEAREST_EXACT)
                mask=classes!=255;w,h=rw,rh
    before_cleanup=classes.copy()
    # Remove specks, while keeping disjoint meaningful islands of each color.
    if separation not in {'exact','solid-inks','compressed-inks'} or not detailed:
        for ci in range(len(centers)):
            n,lab,stats,_=cv2.connectedComponentsWithStats((classes==ci).astype(np.uint8))
            tiny=stats[:,cv2.CC_STAT_AREA]<12
            if detailed:tiny &= stats[:,[cv2.CC_STAT_WIDTH,cv2.CC_STAT_HEIGHT]].max(axis=1)<10
            tiny[0]=False
            if tiny.any():classes[tiny[lab]]=255
    missing=mask&(classes==255)
    if missing.any():
        if np.all(classes==255):
            raise ValueError('分色后只剩细小碎点，请选择保留细节或换用更清晰的平面稿。')
        indices=distance_transform_edt(classes==255,return_distances=False,return_indices=True)
        classes[missing]=classes[tuple(indices[:,missing])]
    # Antialiased boundaries are mixtures, not extra thread colors. Retain a
    # color only when it has a small two-dimensional interior of its own.
    supported=[] if detailed else [i for i in range(len(centers)) if cv2.erode((classes==i).astype(np.uint8),np.ones((3,3),np.uint8)).sum()>=8]
    if supported:
        fringe=mask&~np.isin(classes,supported)
        classes[fringe]=255
        if fringe.any():
            indices=distance_transform_edt(classes==255,return_distances=False,return_indices=True)
            classes[fringe]=classes[tuple(indices[:,fringe])]
    counts=np.array([(classes==i).sum() for i in range(len(centers))])
    cleanup_changed=int(np.count_nonzero(mask & (classes!=before_cleanup)))
    if detailed:
        audit=np.zeros((*classes.shape,4),np.uint8);audit[mask & (classes!=before_cleanup)]=[224,54,119,230]
        Image.fromarray(audit).save(out/'detail-cleanup.png')
    used=np.where(counts>0)[0]
    # Broad areas first, dark details next, tiny white highlights last.
    def order(i):
        light=centers[i,0];fraction=counts[i]/mask.sum()
        return (3 if light>87 and fraction<.05 else 2 if light<28 else 0,-int(counts[i]))
    ordered=sorted(used,key=order);old=classes.copy();colors=[]
    for ci,previous in enumerate(ordered):
        classes[old==previous]=ci;r=rgbs[previous].tolist()
        colors.append({'key':f'color-{ci+1}','name':color_name(r),'hex':'#'+''.join(f'{v:02X}' for v in r),'rgb':r,'pixel_count':int(counts[previous])})
    match_threads(colors)
    assert 1<=len(colors)<=max_colors<=9
    rgba=np.zeros((*classes.shape,4),np.uint8)
    for i,c in enumerate(colors):rgba[classes==i]=[*c['rgb'],255]
    Image.fromarray(rgba).save(out/'flat.png');np.save(out/'classes.npy',classes)
    meta={'width_px':classes.shape[1],'height_px':classes.shape[0],'source_bbox_px':bbox,'colors':colors,'max_colors':max_colors,'style':'rounded_flat_color_blocks','detail_mode':detail_mode,'cleanup_changed_pixels':cleanup_changed,'palette_assignment':separation,'background_handling':'alpha' if has_alpha else 'auxiliary_color' if background_color else 'preserve_white' if preserve_white else 'edge_white'}
    if registration is not None:meta['revision_registration']=registration
    save_json(out/'palette.json',meta)
    view=artwork_view(out,width,thread)
    write_svg(view,out/'layers.svg');save_json(out/'preview.json',view)
    return meta,view

def ring_path(coords):
    p=list(coords)
    return 'M'+' L'.join(f'{x:.3f},{y:.3f}' for x,y in p)+' Z'

def poly_path(p):
    if p.geom_type=='MultiPolygon':return ' '.join(poly_path(x) for x in p.geoms)
    return ring_path(p.exterior.coords)+' '+' '.join(ring_path(r.coords) for r in p.interiors)

def detail_geometries(classes,ci):
    # Trace cell edges rather than pixel centres. Diagonal thin lines remain
    # connected and tiny holes are not converted into self-touching polygons.
    mask=(classes==ci).view(np.uint8)
    x,y,w,h=cv2.boundingRect(mask)
    if not w:return []
    contours=find_contours(np.pad(mask[y:y+h,x:x+w],1),.5,fully_connected='high')
    outers=[];holes=[]
    for index,c in enumerate(contours):
        pixel=c[:,::-1]+[x-1,y-1]
        signed=np.sum(pixel[:-1,0]*pixel[1:,1]-pixel[1:,0]*pixel[:-1,1])/2
        if abs(signed)<.5:continue
        if signed>0:outers.append((index,Polygon(pixel)))
        else:holes.append(Polygon(pixel))
    if not outers:return []
    tree=STRtree([p for _,p in outers]);inside={i:[] for i in range(len(outers))}
    for hole in holes:
        owners=tree.query(hole,predicate='covered_by')
        if len(owners):inside[min(owners,key=lambda i:outers[i][1].area)].append(hole.exterior.coords)
    result=[]
    for i,(index,outer) in enumerate(outers):
        shape=Polygon(outer.exterior.coords,inside[i]).simplify(.34,preserve_topology=True)
        if not shape.is_valid:shape=shape.buffer(0)
        parts=list(shape.geoms) if shape.geom_type=='MultiPolygon' else [shape]
        for j,p in enumerate(parts):
            if p.geom_type=='Polygon' and p.area>.49:result.append((p,f'c{ci+1}-r{index+1}-s{j+1}'))
    return result

def extract_geometries(folder, width, with_ids=False):
    folder=Path(folder)
    fingerprint=tuple((s.st_mtime_ns,s.st_size) for s in ((folder/name).stat() for name in ('classes.npy','palette.json')))
    meta,geometries,height,identities=_cached_geometries(str(folder.resolve()),width,fingerprint)
    result=(copy.deepcopy(meta),[list(g) for g in geometries],height,[list(g) for g in identities])
    return result if with_ids else result[:3]

@lru_cache(maxsize=4)
def _pixel_geometry(folder,fingerprint):
    meta=json.loads((Path(folder)/'palette.json').read_text());classes=np.load(Path(folder)/'classes.npy')
    groups=[]
    for ci in range(len(meta['colors'])):
        if meta.get('detail_mode')=='preserve':
            groups.append(detail_geometries(classes,ci));continue
        contours,hierarchy=cv2.findContours((classes==ci).astype(np.uint8),cv2.RETR_CCOMP,cv2.CHAIN_APPROX_NONE)
        group=[];groups.append(group)
        if hierarchy is None:continue
        def points(c):
            p=c[:,0,:].astype(float)
            if len(p)>8:p=gaussian_filter1d(p,1.15,axis=0,mode='wrap')
            return cv2.approxPolyDP(p.astype(np.float32),.4,True)[:,0,:]
        for i,c in enumerate(contours):
            if hierarchy[0][i][3]!=-1 or cv2.contourArea(c)<8:continue
            holes=[];j=hierarchy[0][i][2]
            while j>=0:
                if cv2.contourArea(contours[j])>5:holes.append(points(contours[j]))
                j=hierarchy[0][j][0]
            outer=points(c)
            if len(outer)<3:continue
            p=Polygon(outer,[h for h in holes if len(h)>=3]).buffer(0)
            pieces=list(p.geoms) if p.geom_type=='MultiPolygon' else [p]
            group.extend((g,f'c{ci+1}-r{i+1}-s{j+1}') for j,g in enumerate(pieces) if g.geom_type=='Polygon')
    return meta,groups

@lru_cache(maxsize=6)
def _cached_geometries(folder,width,fingerprint):
    meta,pixel_groups=_pixel_geometry(folder,fingerprint)
    scale=width/max(1,meta['width_px']-1);height=(meta['height_px']-1)*scale
    detailed=meta.get('detail_mode')=='preserve'
    geometries=[];identities=[]
    for pixel_group in pixel_groups:
        group=[]
        for shape,key in pixel_group:
            p=affine_transform(shape,[scale,0,0,scale,5,5])
            if detailed:group.append((p,key))
            elif p.area>.025:group.append((p.simplify(.018,preserve_topology=True),key))
        group.sort(key=lambda pair:(pair[0].centroid.y,pair[0].centroid.x))
        geometries.append([p for p,_ in group]);identities.append([key for _,key in group])
    return meta,geometries,height,identities

def artwork_view(folder,width,thread='105D',spacing=.42):
    info,geo,height,identities=extract_geometries(folder,width,with_ids=True)
    objects=[]
    for ci,group in enumerate(geo):
        for n,p in enumerate(group):objects.append({'id':f'c{ci+1}-p{n+1}','region_key':identities[ci][n],'color':ci,'path':poly_path(p),'area_mm2':round(p.area,3),'bbox':list(p.bounds)})
    return artwork_canvas(info,width,height,thread,spacing,objects)

def artwork_canvas(info,width,height,thread,spacing,objects):
    meta={'design_width_mm':width,'design_height_mm':round(height,2),'page_width_mm':width+10,'page_height_mm':height+10,'thread_spec':thread,'row_spacing_mm':spacing,'colors':info['colors'],'separated_objects':objects,'production_ready':False,'source':'current_selected_version','detail_mode':info.get('detail_mode','simplify'),'cleanup_changed_pixels':info.get('cleanup_changed_pixels')}
    meta['source_width_px']=info['width_px']
    return {'meta':meta,'width':width+10,'height':height+10,'stats':None,'commands':[],'prefix':[]}

def svg_root(width,height):
    root=ET.Element(f'{{{SVG}}}svg',nsmap={None:SVG,'inkscape':INK,'inkstitch':STITCH},width=f'{width}mm',height=f'{height}mm',viewBox=f'0 0 {width} {height}')
    meta=ET.SubElement(root,f'{{{SVG}}}metadata')
    # Preserve the 0.18–0.22 mm turns between adjacent fill rows. A 0.30 mm
    # filter deletes these endpoints even when skip_last is explicitly false.
    for k,v in [('inkstitch_svg_version','3'),('min_stitch_len_mm','0.1'),('collapse_len_mm','3.0')]:ET.SubElement(meta,f'{{{STITCH}}}{k}').text=v
    return root

def write_svg(view,path):
    root=svg_root(view['width'],view['height'])
    for ci,color in enumerate(view['meta']['colors']):
        group=ET.SubElement(root,f'{{{SVG}}}g',id=f'color-{ci+1}');group.set(f'{{{INK}}}label',color['name']);group.set(f'{{{INK}}}groupmode','layer')
        for o in view['meta']['separated_objects']:
            if o['color']==ci:ET.SubElement(group,f'{{{SVG}}}path',id=o['id'],d=o['path'],fill=color['hex'],**{'fill-rule':'evenodd'})
    ET.ElementTree(root).write(str(path),encoding='utf-8',xml_declaration=True)
    return root

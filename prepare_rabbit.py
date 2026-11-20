"""Single-image embroidery experiment; never labels outputs production-ready."""
from pathlib import Path
import json, re, shutil, argparse
import cv2
import numpy as np
from PIL import Image
from lxml import etree as ET
from shapely.geometry import Polygon, LineString, Point
from shapely.ops import unary_union
from skimage.color import rgb2lab, deltaE_ciede2000

BASE = Path(__file__).parent / 'rabbit-test-20261114'
parser=argparse.ArgumentParser()
parser.add_argument('--width',type=float,default=60)
parser.add_argument('--thread',choices=['75D','105D'],default='105D')
parser.add_argument('--spacing',type=float)
parser.add_argument('--out',type=Path,default=BASE)
args=parser.parse_args()
OUT=args.out.resolve();OUT.mkdir(parents=True,exist_ok=True)
WIDTH=args.width
SPACING=args.spacing if args.spacing is not None else {'75D':.35,'105D':.42}[args.thread]
assert 25<=WIDTH<=140 and .25<=SPACING<=.60
APP = Path('/Users/chenwenjie/Library/Application Support/org.inkscape.Inkscape/config/inkscape/extensions/inkstitch/inkstitch.app')
SVG = 'http://www.w3.org/2000/svg'
INK = 'http://www.inkscape.org/namespaces/inkscape'
STITCH = 'http://inkstitch.org/namespace'
NSMAP = {None: SVG, 'inkscape': INK, 'inkstitch': STITCH}
COLORS = [
    {'key':'yellow','name':'浅黄','hex':'#F6E68C'},
    {'key':'pink','name':'粉色','hex':'#EB939B'},
    {'key':'black','name':'黑色','hex':'#1B1B20'},
    {'key':'white','name':'白色','hex':'#FFFFFF'},
]

def rgb(h): return [int(h[i:i+2],16) for i in (1,3,5)]
def ring_path(coords, close=True):
    a = list(coords)
    return 'M' + ' L'.join(f'{x:.4f},{y:.4f}' for x,y in a) + (' Z' if close else '')
def poly_path(poly):
    if poly.geom_type=='MultiPolygon':return ' '.join(poly_path(p) for p in poly.geoms)
    return ring_path(poly.exterior.coords) + ' ' + ' '.join(ring_path(r.coords) for r in poly.interiors)
def clean(poly):
    if not poly.is_valid: poly = poly.buffer(0)
    if poly.geom_type == 'MultiPolygon': poly = max(poly.geoms,key=lambda p:p.area)
    return poly.simplify(0.035,preserve_topology=True)

active=json.loads((BASE/'active-master.json').read_text()) if (BASE/'active-master.json').exists() else {'classes_file':'original-classes.npy','master':'original-separated.png','method':'original_pixel_segmentation'}
classes=np.load(BASE/active['classes_file'])
opaque = classes!=255
yy,xx=np.nonzero(opaque)
x0,x1,y0,y1=int(xx.min()),int(xx.max()),int(yy.min()),int(yy.max())
scale=WIDTH/(x1-x0)
source_bounds=active.get('source_bounds_px',[x0,y0,x1,y1])
design_bounds=active.get('design_bounds_px',source_bounds)
height=WIDTH*(design_bounds[3]-design_bounds[1])/(design_bounds[2]-design_bounds[0])
scale_y=height/(y1-y0)

def mask_polys(mask, fill_holes=False):
    contours,hierarchy=cv2.findContours(mask.astype(np.uint8),cv2.RETR_CCOMP,cv2.CHAIN_APPROX_SIMPLE)
    answer=[]
    if hierarchy is None: return answer
    h=hierarchy[0]
    def points(c):
        a=cv2.approxPolyDP(c,.45,True)[:,0,:].astype(float)
        a[:,0]=(a[:,0]-x0)*scale+5
        a[:,1]=(a[:,1]-y0)*scale_y+5
        return a
    for i,c in enumerate(contours):
        if h[i][3]!=-1 or cv2.contourArea(c)<30:continue
        outer=points(c)
        holes=[]
        j=h[i][2]
        while j>=0:
            if cv2.contourArea(contours[j])>20 and not fill_holes: holes.append(points(contours[j]))
            j=h[j][0]
        poly=clean(Polygon(outer,holes))
        if poly.area>.045: answer.append(poly)
    return sorted(answer,key=lambda p:(p.centroid.y,p.centroid.x))

geometries=[mask_polys(classes==i) for i in range(4)]
gpl=APP/'Contents/Resources/palettes/InkStitch Madeira Rayon.gpl'
threads=[]
for line in gpl.read_text().splitlines():
    m=re.match(r'^\s*(\d+)\s+(\d+)\s+(\d+)\s+(.+?)\s+(\S+)\s*$',line)
    if m:
        r,g,b=map(int,m.group(1,2,3));threads.append({'rgb':[r,g,b],'name':m.group(4),'code':m.group(5)})
labs=rgb2lab(np.array([t['rgb'] for t in threads],float)/255)
for c in COLORS:
    lab=rgb2lab(np.array(rgb(c['hex']),float)/255)
    diffs=deltaE_ciede2000(labs,np.broadcast_to(lab,labs.shape))
    idx=int(diffs.argmin());match=dict(threads[idx]);match['delta_e_2000']=round(float(diffs[idx]),2)
    match['hex']='#'+''.join(f'{v:02X}' for v in match['rgb'])
    c['thread']=match
    c['brand']='Madeira Rayon（演示色卡）'
shutil.copy2(gpl,OUT/'demo-thread-palette.gpl')

def svg_root():
    root=ET.Element(f'{{{SVG}}}svg',nsmap=NSMAP,width=f'{WIDTH+10}mm',height=f'{height+10:.4f}mm',viewBox=f'0 0 {WIDTH+10} {height+10:.4f}')
    meta=ET.SubElement(root,f'{{{SVG}}}metadata')
    ET.SubElement(meta,f'{{{STITCH}}}inkstitch_svg_version').text='3'
    ET.SubElement(meta,f'{{{STITCH}}}min_stitch_len_mm').text='0.3'
    ET.SubElement(meta,f'{{{STITCH}}}collapse_len_mm').text='3.0'
    return root
def label(el,name):el.set(f'{{{INK}}}label',name)
def param(el,key,val):el.set(f'{{{STITCH}}}{key}',str(val).lower() if isinstance(val,bool) else str(val))

flat=svg_root(); objects=[]
for ci,c in enumerate(COLORS):
    group=ET.SubElement(flat,f'{{{SVG}}}g',id='color-'+c['key']);label(group,c['name']);group.set(f'{{{INK}}}groupmode','layer')
    for n,p in enumerate(geometries[ci]):
        pid=f'{c["key"]}-{n+1}'
        ET.SubElement(group,f'{{{SVG}}}path',id=pid,d=poly_path(p),fill=c['hex'],**{'fill-rule':'evenodd'})
        objects.append({'id':pid,'color':ci,'path':poly_path(p),'area_mm2':round(p.area,3),'bbox':list(p.bounds)})
ET.ElementTree(flat).write(str(OUT/'rabbit-layers.svg'),encoding='utf-8',xml_declaration=True)

planned=svg_root(); planned_objects=[]
def add_fill(poly,ci,pid,name,angle=0,underlay=False):
    c=COLORS[ci]
    p=ET.SubElement(planned,f'{{{SVG}}}path',id=pid,d=poly_path(poly),fill=c['thread']['hex'],**{'fill-rule':'evenodd'})
    label(p,name)
    for k,v in {'auto_fill':True,'fill_method':'auto_fill','angle':angle,'row_spacing_mm':SPACING,'max_stitch_length_mm':3.2,'running_stitch_length_mm':2.2,'fill_underlay':underlay,'fill_underlay_row_spacing_mm':1.8,'fill_underlay_angle':90,'fill_underlay_max_stitch_length_mm':3.5,'expand_mm':.10,'trim_after':True}.items():param(p,k,v)
    planned_objects.append({'id':pid,'name':name,'color':ci,'stitch_type':'填针','path':poly_path(poly)})
    return p

# The yellow base is a continuous area, with overlaid details. This is an explicit
# experiment rule, not an inferred reproduction of the photo's original stitches.
yellow=max(geometries[0],key=lambda p:p.area)
yellow=Polygon(yellow.exterior)
add_fill(yellow,0,'base-yellow','黄色底面',angle=0,underlay=True)
for n,p in enumerate(geometries[1]):
    pos=p.centroid
    relx=(pos.x-5)/WIDTH;rely=(pos.y-5)/height
    name=('左耳内' if relx<.5 else '右耳内') if rely<.4 else ('舌头' if .4<relx<.6 and rely>.72 else ('左腮红' if relx<.5 else '右腮红'))
    add_fill(Polygon(p.exterior),1,f'pink-fill-{n+1}',name,angle=0 if pos.y<height*.55 else 25)

border=max(geometries[2],key=lambda p:p.area)
inside=[p for p in geometries[2] if p is not border]
for n,p in enumerate(inside):
    # Keep holes for the white glints and tongue, using slight edge expansion.
    add_fill(p,2,f'black-detail-{n+1}',f'黑色细节 {n+1}',angle=90)

# Turn the outer black ring into two corresponding satin rails. Nearest inner
# points are checked for order/width later in the exported stitch statistics.
outer=LineString(border.exterior.coords)
inner=LineString(max(border.interiors,key=lambda r:Polygon(r).area).coords)
count=max(80,int(outer.length/.7))
rail_a=[];rail_b=[]
for distance in np.linspace(0,outer.length,count):
    pt=outer.interpolate(distance)
    ip=inner.interpolate(inner.project(pt))
    rail_a.append((pt.x,pt.y));rail_b.append((ip.x,ip.y))
# The nearest-point rails can leave wedges at concave junctions. Detect those
# geometric gaps and fill only the uncovered wedges before sewing the satin.
strips=[]
for j in range(len(rail_a)-1):
    strip=Polygon([rail_a[j],rail_a[j+1],rail_b[j+1],rail_b[j]]).buffer(0)
    if not strip.is_empty:strips.append(strip)
gaps=border.difference(unary_union(strips).buffer(.13)).buffer(.10).intersection(border)
gap_parts=list(gaps.geoms) if hasattr(gaps,'geoms') else [gaps]
gap_parts=[gap for gap in gap_parts if gap.geom_type=='Polygon' and gap.area>.20]
if gap_parts:add_fill(unary_union(gap_parts),2,'outline-corners','轮廓转角补针',angle=35)
p=ET.SubElement(planned,f'{{{SVG}}}path',id='outline-satin',d=ring_path(rail_a,False)+' '+ring_path(rail_b,False),fill='none',stroke=COLORS[2]['thread']['hex'],**{'stroke-width':'.15'})
label(p,'黑色外轮廓缎面针')
for k,v in {'satin_column':True,'zigzag_spacing_mm':SPACING,'pull_compensation_mm':.10,'center_walk_underlay':True,'contour_underlay':False,'zigzag_underlay':False,'trim_after':True,'max_stitch_length_mm':6}.items():param(p,k,v)
planned_objects.append({'id':'outline-satin','name':'黑色外轮廓','color':2,'stitch_type':'缎面针','path':poly_path(border)})
for n,p in enumerate(geometries[3]):add_fill(p,3,f'white-glint-{n+1}',f'眼睛高光 {n+1}',angle=0)
ET.ElementTree(planned).write(str(OUT/'rabbit-embroidery.svg'),encoding='utf-8',xml_declaration=True)
metadata={'test_date':'2026-11-14','design_width_mm':WIDTH,'design_height_mm':round(height,2),'page_width_mm':WIDTH+10,'page_height_mm':round(height+10,4),'source':'original.jpg','master':active['master'],'method':active['method'],'image_model':active.get('model'),'master_bounds_px':[x0,y0,x1,y1],'source_bounds_px':source_bounds,'thread_spec':args.thread,'row_spacing_mm':SPACING,'thread_profile_status':'experimental_uncalibrated','colors':COLORS,'separated_objects':objects,'planned_objects':planned_objects,'production_ready':False,'assumptions':['尺寸由用户设定；尚未获得实物尺寸','以用户选定的圆润平面稿为母版，保留该稿比例；再进行四色分层与排针','75D/105D为线规格选项，0.35/0.42mm是可修改的试排间距，不是D数换算的直径，也不是该线材的厂商参数','暂按稳定机织底布试排，未试绣；密度须按实际线材、底布校准','Madeira Rayon为演示色卡；需按实际品牌和实体线卡确认']}
(OUT/'design.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2))
print(json.dumps({'dimensions_mm':[WIDTH,round(height,2)],'thread':args.thread,'spacing':SPACING,'components':[len(x) for x in geometries],'objects':len(objects),'planned_objects':len(planned_objects)},ensure_ascii=False))

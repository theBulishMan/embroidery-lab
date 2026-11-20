"""Photo-coordinate color masks for this supplied sample; no image generation.

The broad ROI and two eye windows are explicit sample-specific annotations.
All final boundaries come from pixels. Smoothing removes stitch texture.
"""
from pathlib import Path
import json
import cv2
import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import binary_fill_holes

B = Path(__file__).parent / 'rabbit-test-20261114'
src = np.array(Image.open(B/'original.jpg').convert('RGB'))
smooth = cv2.medianBlur(src, 5)
smooth = cv2.GaussianBlur(smooth, (5,5), 1.0)
gray = cv2.cvtColor(smooth, cv2.COLOR_RGB2GRAY)
lab = cv2.cvtColor(smooth, cv2.COLOR_RGB2LAB)
roi = np.zeros(src.shape[:2],np.uint8)
coarse = np.array([(578,260),(600,266),(614,285),(619,313),(617,388),
    (620,445),(623,466),(636,472),(643,461),(644,403),(642,343),
    (643,302),(652,272),(674,260),(696,260),(715,274),(729,302),
    (734,343),(735,400),(726,470),(721,489),(755,499),(808,531),
    (856,575),(892,620),(913,665),(921,714),(918,768),(903,823),
    (872,865),(827,902),(770,930),(699,947),(618,950),(541,947),
    (469,932),(406,907),(364,874),(338,837),(321,790),(315,744),
    (317,687),(335,631),(364,582),(410,537),(464,505),(534,486),
    (532,455),(527,400),(527,342),(533,303),(549,275)], np.int32)
cv2.fillPoly(roi,[coarse],1)
roi=cv2.dilate(roi,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(9,9)))
kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(5,5))
dark = ((gray<110) & (roi>0)).astype(np.uint8)
dark = cv2.morphologyEx(dark,cv2.MORPH_CLOSE,kernel)
contours,_ = cv2.findContours(dark,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
outline = max(contours,key=cv2.contourArea)
silhouette = np.zeros_like(roi)
cv2.drawContours(silhouette,[outline],-1,1,-1)
silhouette = cv2.morphologyEx(silhouette,cv2.MORPH_OPEN,kernel)>0

def tidy(mask, minimum=30, close=3):
    k=cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(close,close))
    m=cv2.morphologyEx(mask.astype(np.uint8),cv2.MORPH_CLOSE,k)
    m=cv2.medianBlur(m,3)
    n,labels,stats,_=cv2.connectedComponentsWithStats(m)
    return np.isin(labels,[i for i in range(1,n) if stats[i,cv2.CC_STAT_AREA]>=minimum])

black=tidy((gray<95)&silhouette,minimum=35,close=3)
# Preserve the three separate cheek marks. Here the photographed pink gaps are
# only a few pixels wide, so closing would accidentally fuse the black marks.
for xa,ya,xb,yb in [(410,708,506,764),(725,710,817,771)]:
    black[ya:yb,xa:xb]=tidy(gray[ya:yb,xa:xb]<73,minimum=20,close=1)
pink=tidy((lab[:,:,1]>137)&(gray>100)&silhouette,minimum=40,close=7)
for xa,ya,xb,yb in [(535,300,585,475),(674,300,724,491),(585,754,639,808)]:
    pink[ya:yb,xa:xb]=tidy((lab[ya:yb,xa:xb,1]>132)&(gray[ya:yb,xa:xb]>100)&silhouette[ya:yb,xa:xb],minimum=20,close=5)
# A small eye-only ROI stops gray highlights on the outside felt from becoming
# extra white color blocks. The highlighted pixels themselves define the paths.
eyes=np.zeros_like(roi)
cv2.ellipse(eyes,(530,675),(39,45),0,0,360,1,-1)
cv2.ellipse(eyes,(697,679),(40,45),0,0,360,1,-1)
white=tidy((gray>145)&(lab[:,:,2]<145)&(eyes>0)&silhouette,minimum=16,close=3)
# The outer border is a continuous sewn ring. Retain its largest interior and
# fill only texture holes, so specular gray stitches do not become yellow dots.
ring=cv2.morphologyEx(((gray<110)&silhouette).astype(np.uint8),cv2.MORPH_CLOSE,kernel)
cc,labels,stats,_=cv2.connectedComponentsWithStats(ring)
ring=labels==(1+np.argmax(stats[1:,cv2.CC_STAT_AREA]))
interior=binary_fill_holes(ring)&~ring
cc,labels,stats,_=cv2.connectedComponentsWithStats(interior.astype(np.uint8))
if cc<2:raise RuntimeError('Outer border did not close; check source mask')
inside=labels==(1+np.argmax(stats[1:,cv2.CC_STAT_AREA]))
black|=silhouette&~inside
# Fill tiny within-feature gaps; retain the independently detected glints and
# tongue as their own layers. Shape locations never move.
eye_black=black&(eyes>0)
eye_black=binary_fill_holes(eye_black)
black|=eye_black
pink_contours,_=cv2.findContours(pink.astype(np.uint8),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
for c in pink_contours:
    if cv2.contourArea(c)>80:cv2.drawContours(pink.view(np.uint8),[c],-1,1,-1)
classes=np.full(roi.shape,255,np.uint8)
classes[silhouette]=0
classes[pink]=1
classes[black]=2
classes[white]=3
palette=np.array([[246,230,140],[235,147,155],[27,27,32],[255,255,255]],np.uint8)
flat=np.zeros((*roi.shape,4),np.uint8)
for i in range(4):flat[classes==i]=[*palette[i],255]
Image.fromarray(flat).save(B/'original-separated.png')
np.save(B/'original-classes.npy',classes)
ys,xs=np.where(silhouette)
bounds=[int(xs.min()),int(ys.min()),int(xs.max()),int(ys.max())]
crop=(bounds[0]-20,bounds[1]-20,bounds[2]+21,bounds[3]+21)
photo=Image.fromarray(src).crop(crop)
mask=Image.new('RGB',photo.size,'#f8f7f3')
mask.paste(Image.fromarray(flat).crop(crop),mask=Image.fromarray(flat).crop(crop).getchannel('A'))
comparison=Image.new('RGB',(photo.width*2+24,photo.height),'white')
comparison.paste(photo,(0,0));comparison.paste(mask,(photo.width+24,0))
comparison.save(B/'original-vs-separated.png')
meta={'source':'original.jpg','method':'original_pixel_segmentation','sample_specific_roi':True,
      'bounds_px':bounds,'crop_px':list(crop),'colors':4,
      'smoothing':'5px median, 1px Gaussian, small morphology; no generative redraw',
      'color_pixel_counts':[int((classes==i).sum()) for i in range(4)]}
(B/'segmentation.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2))
print(json.dumps(meta,ensure_ascii=False))

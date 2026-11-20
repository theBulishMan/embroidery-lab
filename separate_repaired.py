"""Separate the user-requested AI-restored master into four closed colors."""
from pathlib import Path
import json
import numpy as np
import cv2
from PIL import Image

B=Path(__file__).parent/'rabbit-test-20261114'
MASTER='user-approved-master.png'
a=np.asarray(Image.open(B/MASTER).convert('RGBA'))
mask=(a[:,:,3]>240).astype(np.uint8)
n,labels,stats,_=cv2.connectedComponentsWithStats(mask)
mask=labels==(1+np.argmax(stats[1:,cv2.CC_STAT_AREA]))
palette=np.array([[246,230,140],[235,147,155],[27,27,32],[255,255,255]],np.uint8)
dist=((a[:,:,:3,None].astype(float)-palette.T[None,None,:,:])**2).sum(axis=2)
classes=np.argmin(dist,axis=2).astype(np.uint8);classes[~mask]=255
np.save(B/'repaired-classes.npy',classes)
flat=np.zeros((*classes.shape,4),np.uint8)
for i in range(4):flat[classes==i]=[*palette[i],255]
Image.fromarray(flat).save(B/'repaired-separated.png')
y,x=np.where(mask);bounds=[int(x.min()),int(y.min()),int(x.max()),int(y.max())]
original=json.loads((B/'segmentation.json').read_text())
meta={'classes_file':'repaired-classes.npy','master':MASTER,'method':'approved_flat_artwork_color_separation',
      'bounds_px':bounds,'design_bounds_px':bounds,'source_bounds_px':original['bounds_px'],
      'registration':'使用用户选定的圆润平面稿及其原有比例；分层与该稿叠图核对',
      'colors':4}
(B/'active-master.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2))
print(meta)

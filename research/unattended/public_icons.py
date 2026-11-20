"""Frozen independent set of common artwork. Original SVGs: Twemoji v14.0.2 (CC-BY 4.0)."""
from pathlib import Path
import shutil
FIXTURES={
 'pubapple':('1f34e','苹果',32,'105D'), 'pubcarrot':('1f955','胡萝卜',38,'75D'),
 'pubcherries':('1f352','樱桃',40,'105D'), 'pubpencil':('270f','铅笔',32,'75D'),
 'pubbulb':('1f4a1','灯泡',42,'105D'), 'pubpin':('1f4cc','图钉',28,'75D'),
 'pubbus':('1f68c','公交车',52,'75D'), 'pubumbrella':('2602','雨伞',45,'105D'),
 'pubtennis':('1f3be','网球',45,'75D'), 'pubcamera':('1f4f7','相机',48,'105D')}
SAMPLES={k:(None,w,t) for k,(_,_,w,t) in FIXTURES.items()}
# Originally reserved for transfer; its mug and flower later exposed two faults.
FRESH={
 'freshleaf':('1f343','树叶',36,'75D'), 'freshballoon':('1f388','气球',32,'105D'),
 'freshfish':('1f41f','鱼',48,'75D'), 'freshguitar':('1f3b8','吉他',55,'75D'),
 'freshmug':('2615','咖啡杯',40,'105D'), 'freshflower':('1f33c','花朵',36,'75D')}
TRANSFER={k:(None,w,t) for k,(_,_,w,t) in FRESH.items()}
# Locked before evaluating the cloth-opening and repeated-fill corrections.
AUDIT={
 'auditstar':('2b50','星星',28,'75D'), 'auditdonut':('1f369','甜甜圈',40,'105D'),
 'auditlock':('1f512','挂锁',35,'75D'), 'auditshoe':('1f45f','运动鞋',52,'105D'),
 'auditbug':('1f41e','瓢虫',38,'75D'), 'audithouse':('1f3e0','房子',50,'105D'),
 'auditnotes':('1f3b5','音符',30,'75D'), 'auditsunflower':('1f33b','向日葵',48,'105D')}
UNSEEN={k:(None,w,t) for k,(_,_,w,t) in AUDIT.items()}
FORMATS={'pencil20':('pubpencil',20,'75D'),'pencil60':('pubpencil',60,'75D'),
         'applejpg':('pubapple',32,'105D'),'whiteboardjpg':('pubapple',32,'105D'),'penciljpg':('pubpencil',32,'75D'),'camerajpg':('pubcamera',48,'105D')}
VARIANTS={k:(None,w,t) for k,(_,w,t) in FORMATS.items()}
DIR=Path(__file__).with_name('public-icons')
def image(name,out):
    from PIL import Image
    source=FORMATS.get(name,(name,))[0]
    src=DIR/((FIXTURES|FRESH|AUDIT)[source][0]+'.png')
    if name.endswith('jpg'):
        with Image.open(src).convert('RGBA') as im:
            bg=Image.new('RGB',im.size,'white');bg.paste(im,mask=im.getchannel('A'));bg.save(out,'JPEG',quality=88)
    else:shutil.copyfile(src,out)


"""Unseen shapes for a final check: freeze sizes and inputs before computing any result."""
import math
SAMPLES={'acorn':(None,32,'75D'),'magnifier':(None,29,'105D'),'ladybug':(None,38,'75D'),'shoe':(None,32,'75D'),'teapot':(None,45,'105D'),'harmonica':(None,24,'105D')}

def draw(name,d):
 k='#26313a';g='#4a876d';o='#c7854e';a='#efb755';b='#6190b3';w='#ffefce';r='#cf6f72';clear=(0,0,0,0)
 if name=='acorn':
  d.polygon([(119,170),(301,170),(284,265),(251,314),(211,353),(167,312),(133,267)],fill=o);d.line([(199,153),(216,93),(248,63)],fill=k,width=23);d.rounded_rectangle((88,125,327,216),39,fill=g)
 elif name=='magnifier':
  d.line([(245,251),(342,346)],fill=o,width=36);d.ellipse((51,42,287,282),fill=b);d.ellipse((76,68,262,256),fill=clear)
 elif name=='ladybug':
  d.ellipse((147,65,270,168),fill=k);d.ellipse((89,106,329,358),fill=k);d.ellipse((102,119,316,345),fill=r);d.line([(209,124),(209,341)],fill=k,width=9)
  for x,y,rad in [(160,171,17),(261,175,17),(137,236,21),(278,240,21),(176,297,12),(243,295,12)]:d.ellipse((x-rad,y-rad,x+rad,y+rad),fill=k)
 elif name=='shoe':
  d.polygon([(60,129),(119,140),(165,181),(208,122),(274,222),(352,233),(379,250),(378,286),(47,286)],fill=b);d.rounded_rectangle((43,275,381,307),9,fill=w)
  for x,y in [(191,155),(207,171),(224,187),(240,203)]:d.line([(x,y+19),(x+23,y)],fill=w,width=7)
  d.line([(61,247),(126,247),(170,272)],fill=k,width=8)
 elif name=='teapot':
  d.ellipse((248,144,376,275),fill=b);d.ellipse((274,164,350,251),fill=clear);d.polygon([(128,180),(56,152),(33,157),(89,241),(141,278)],fill=b);d.ellipse((94,126,311,321),fill=b);d.ellipse((121,113,285,157),fill=k);d.ellipse((187,83,220,122),fill=a);d.rounded_rectangle((151,304,254,322),6,fill=k)
 elif name=='harmonica':
  d.rounded_rectangle((36,135,383,285),18,fill=k);d.rounded_rectangle((50,147,369,268),11,fill=b)
  for x in range(66,347,35):d.rounded_rectangle((x,187,x+20,241),4,fill=k)
  d.line([(71,165),(347,165)],fill=w,width=5)
 else:raise KeyError(name)

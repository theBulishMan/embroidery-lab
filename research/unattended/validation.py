"""Independent, fixed transfer set. Freeze shapes, sizes and threads before running any of them."""
import math
SAMPLES={'rocket':(None,40,'75D'),'turtle':(None,48,'105D'),'crescent':(None,26,'75D'),'gear':(None,46,'105D'),'fox':(None,38,'75D'),'key':(None,30,'105D')}
def draw(name,d):
 k='#26384b';g='#569477';o='#dc8655';y='#efbe5d';b='#648ab2';w='#faeed3';clear=(0,0,0,0)
 if name=='rocket':
  d.polygon([(150,225),(101,276),(92,338),(168,295)],fill=o);d.polygon([(252,225),(300,280),(313,337),(236,297)],fill=o);d.polygon([(175,290),(211,379),(240,290)],fill=y);d.polygon([(147,271),(152,194),(173,114),(211,56),(245,111),(270,192),(271,271),(235,306),(180,306)],fill=w);d.ellipse((170,148,249,227),fill=k);d.ellipse((182,160,237,215),fill=b)
 elif name=='turtle':
  for x,z in [(100,106),(100,256),(249,106),(249,256)]:d.ellipse((x,z,x+56,z+63),fill=g)
  d.ellipse((286,164,370,239),fill=g);d.ellipse((336,184,351,199),fill=k);d.ellipse((56,99,311,304),fill=k);d.ellipse((72,113,297,290),fill=g)
  for x,z in [(115,172),(181,142),(181,206),(247,169)]:d.regular_polygon((x,z,27),6,rotation=30,fill=y)
 elif name=='crescent':
  d.ellipse((73,53,339,360),fill=y);d.ellipse((151,33,359,306),fill=clear);d.polygon([(283,110),(293,141),(324,150),(293,160),(282,194),(270,161),(239,151),(271,140)],fill=b)
 elif name=='gear':
  pp=[]
  for i in range(12):
   for frac,r in [(0,118),(.15,150),(.55,150),(.7,118)]:
    a=(i+frac)*math.tau/12;pp.append((210+r*math.cos(a),210+r*math.sin(a)))
  d.polygon(pp,fill=b);d.ellipse((128,128,292,292),fill=k);d.ellipse((151,151,269,269),fill=clear)
 elif name=='fox':
  d.polygon([(62,62),(155,120),(264,120),(363,59),(332,220),(294,278),(209,347),(116,276),(82,217)],fill=o);d.polygon([(78,85),(133,127),(93,158)],fill=k);d.polygon([(344,84),(323,161),(284,130)],fill=k);d.polygon([(91,219),(165,242),(209,290),(249,240),(326,215),(283,272),(209,341),(139,289)],fill=w);d.ellipse((135,186,159,210),fill=k);d.ellipse((258,186,282,210),fill=k);d.polygon([(188,275),(229,275),(210,302)],fill=k)
 elif name=='key':
  d.ellipse((39,125,193,279),fill=y);d.rounded_rectangle((133,182,372,223),8,fill=y);d.rectangle((274,215,301,255),fill=y);d.rectangle((328,214,359,266),fill=y);d.ellipse((75,161,146,242),fill=clear)
 else:raise KeyError(name)

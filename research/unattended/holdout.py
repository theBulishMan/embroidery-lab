"""Eight fixed PNG fixtures created independently of the tuning examples. Units: 420 px raster; widths: mm."""
SAMPLES={'bolt':(None,24,'105D'),'mushroom':(None,34,'75D'),'fish':(None,42,'105D'),'notes':(None,38,'75D'),'umbrella':(None,36,'75D'),'tulip':(None,46,'105D'),'letter':(None,30,'75D'),'mountain':(None,52,'105D')}
def draw(name,d):
 k='#243342';y='#e7a238';r='#c15c66';b='#598396';w='#fff0d6';g='#508673';clear=(0,0,0,0)
 if name=='bolt':d.polygon([(211,46),(320,46),(244,177),(328,177),(107,377),(166,229),(100,229)],fill=y)
 elif name=='mushroom':
  d.rounded_rectangle((170,180,260,354),24,fill=w);d.chord((62,76,354,341),180,360,fill=r)
  for box in [(120,150,151,176),(187,99,229,141),(258,148,293,177)]:d.ellipse(box,fill=w)
 elif name=='fish':
  d.polygon([(237,214),(349,131),(343,280)],fill=y);d.ellipse((53,127,274,291),fill=b);d.polygon([(136,196),(198,206),(150,258)],fill=g);d.ellipse((90,171,117,199),fill=k)
 elif name=='notes':
  d.polygon([(141,116),(307,78),(307,114),(141,154)],fill=k);d.rectangle((139,132,153,288),fill=k);d.rectangle((293,101,307,262),fill=k);d.ellipse((72,254,152,311),fill=k);d.ellipse((225,230,307,285),fill=k)
 elif name=='umbrella':
  d.line([(207,194),(207,308)],fill=k,width=13);d.arc((152,272,219,344),0,180,fill=k,width=13);d.chord((52,63,363,337),180,360,fill=b)
  for x in [52,130,208,286]:d.pieslice((x,182,x+77,217),180,360,fill=clear)
 elif name=='tulip':
  d.line([(207,190),(207,358)],fill=g,width=17);d.polygon([(207,324),(130,298),(97,231),(165,254)],fill=g);d.polygon([(210,291),(282,217),(317,213),(284,268)],fill=g);d.polygon([(128,89),(166,124),(204,63),(242,122),(279,85),(269,183),(238,212),(183,217),(146,184)],fill=r)
 elif name=='letter':
  d.polygon([(64,346),(167,66),(238,66),(353,346),(287,346),(264,280),(153,280),(128,346)],fill=k);d.polygon([(173,231),(238,231),(204,132)],fill=clear)
 elif name=='mountain':
  d.ellipse((242,53,337,149),fill=y);d.polygon([(35,325),(157,124),(220,209),(264,161),(381,325)],fill=b);d.polygon([(105,210),(157,124),(196,177),(171,166),(159,183),(145,171)],fill=w);d.polygon([(238,193),(264,161),(291,199),(268,187),(260,201)],fill=w)
 else:raise KeyError(name)

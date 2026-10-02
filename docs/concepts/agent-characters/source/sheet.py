import sys
from PIL import Image
d=sys.argv[1]; n=sys.argv[2:]; cols=min(4,len(n)); rows=(len(n)+cols-1)//cols
W=Image.new('RGBA',(cols*400,rows*400),(18,18,20,255))
for i,k in enumerate(n): W.alpha_composite(Image.open(f'{d}/{k}.png').resize((400,400)),((i%cols)*400,(i//cols)*400))
W.save(f'{d}/sheet.png')

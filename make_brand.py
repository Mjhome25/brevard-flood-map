#!/usr/bin/env python3
"""Generate favicon, app icons and the link-preview (Open Graph) image into site/."""
import math, os, sys, glob
from PIL import Image, ImageDraw, ImageFont, ImageFilter
HERE=os.path.dirname(os.path.abspath(__file__)); SITE=os.path.join(HERE,"site"); os.makedirs(SITE,exist_ok=True)
TILEDIR=sys.argv[1] if len(sys.argv)>1 else None
NAVY=(29,78,137); WHITE=(255,255,255)
BOLD="/System/Library/Fonts/Supplemental/Arial Bold.ttf"; REG="/System/Library/Fonts/Supplemental/Arial.ttf"

def drop(size, bg=NAVY, radius_frac=0.22, pad=0):
    S=size*4; im=Image.new("RGBA",(S,S),(0,0,0,0)); d=ImageDraw.Draw(im)
    d.rounded_rectangle([pad,pad,S-1-pad,S-1-pad],radius=int(S*radius_frac),fill=bg)
    cx,cy,r=S/2,S*0.60,S*0.215; top=S*0.17
    # teardrop: circle plus tangent triangle to the tip
    d.ellipse([cx-r,cy-r,cx+r,cy+r],fill=WHITE)
    ang=math.asin(r/(cy-top)); dx=r*math.cos(ang); dy=r*math.sin(ang)
    d.polygon([(cx,top),(cx-dx,cy-dy),(cx+dx,cy-dy)],fill=WHITE)
    # wave
    w=S*0.03; pts=[(cx-r*0.7+i*(1.4*r)/40, cy+r*0.12+math.sin(i/40*2*math.pi)*r*0.12) for i in range(41)]
    d.line(pts,fill=bg,width=int(w),joint="curve")
    return im.resize((size,size),Image.LANCZOS)

for s,name in [(32,"favicon-32.png"),(180,"apple-touch-icon.png"),(192,"icon-192.png"),(512,"icon-512.png")]:
    drop(s,radius_frac=0 if name=="apple-touch-icon.png" else 0.22).save(os.path.join(SITE,name))
drop(256).save(os.path.join(SITE,"favicon.ico"),sizes=[(16,16),(32,32),(48,48),(64,64)])
open(os.path.join(SITE,"favicon.svg"),"w").write('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#1d4e89"/><path d="M32 11c-8 11-14 19-14 27a14 14 0 0 0 28 0c0-8-6-16-14-27z" fill="#fff"/><path d="M22 40c3 2 6 2 10 0s7-2 10 0" fill="none" stroke="#1d4e89" stroke-width="3" stroke-linecap="round"/></svg>')

# ---- Open Graph image 1200x630 ----
W,H=1200,630
og=Image.new("RGB",(W,H),(18,40,70))
if TILEDIR:
    z=12
    def t(lat,lon):
        n=2**z; x=(lon+180)/360*n; la=math.radians(lat); y=(1-math.log(math.tan(la)+1/math.cos(la))/math.pi)/2*n; return x,y
    x0,y0=t(28.62,-80.98); x1,y1=t(27.98,-80.45)
    tx0,ty0,tx1,ty1=int(x0),int(y0),int(x1),int(y1)
    mosaic=Image.new("RGB",((tx1-tx0+1)*256,(ty1-ty0+1)*256),(230,235,240))
    for tx in range(tx0,tx1+1):
        for ty in range(ty0,ty1+1):
            p=os.path.join(TILEDIR,f"{z}_{tx}_{ty}.webp")
            if os.path.exists(p): mosaic.paste(Image.open(p).convert("RGB"),((tx-tx0)*256,(ty-ty0)*256))
    crop=mosaic.crop((int((x0-tx0)*256),int((y0-ty0)*256),int((x1-tx0)*256),int((y1-ty0)*256)))
    # fill the right side, cover-fit
    target_w,target_h=W,H; cw,ch=crop.size; sc=max(target_w/cw,target_h/ch)
    crop=crop.resize((int(cw*sc),int(ch*sc)),Image.LANCZOS)
    cw,ch=crop.size; crop=crop.crop(((cw-target_w)//2,(ch-target_h)//2,(cw-target_w)//2+target_w,(ch-target_h)//2+target_h))
    og.paste(crop,(W-target_w,0))
# left-to-right navy fade over the map
fade=Image.new("RGBA",(W,H),(0,0,0,0)); fd=ImageDraw.Draw(fade)
for x in range(W):
    a = 250 if x<380 else max(0,int(250*(1-(x-380)/480)**1.4)) if x<860 else 0
    fd.line([(x,0),(x,H)],fill=(16,38,68,a))
og=Image.alpha_composite(og.convert("RGBA"),fade)
d=ImageDraw.Draw(og)
icon=drop(112,bg=(41,105,180)); og.alpha_composite(icon,(72,92))
f1=ImageFont.truetype(BOLD,78); f2=ImageFont.truetype(REG,38); f3=ImageFont.truetype(BOLD,28); f4=ImageFont.truetype(REG,26)
d.text((72,238),"Brevard",font=f1,fill=WHITE); d.text((72,322),"Emergency",font=f1,fill=WHITE)
d.text((72,432),"Live road closures & flooding",font=f2,fill=(214,226,240))
pill=Image.new("RGBA",(W,H),(0,0,0,0)); pd=ImageDraw.Draw(pill)
pd.rounded_rectangle([72,512,72+d.textlength("BREVARDEMERGENCY.COM",font=f3)+40,562],radius=25,fill=(255,255,255,38),outline=(255,255,255,90),width=2)
og=Image.alpha_composite(og,pill); d=ImageDraw.Draw(og)
d.text((92,522),"BREVARDEMERGENCY.COM",font=f3,fill=WHITE)
d.text((W-330,H-46),"Map data © OpenStreetMap",font=ImageFont.truetype(REG,18),fill=(60,70,80))
og.convert("RGB").save(os.path.join(SITE,"og.png"),optimize=True)
print("brand assets written:", sorted(os.listdir(SITE)))

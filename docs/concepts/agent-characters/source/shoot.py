import glob, sys, subprocess, time, os
from playwright.sync_api import sync_playwright
from PIL import Image
EXE=glob.glob('/opt/pw-browsers/chromium-*/chrome-linux*/chrome')[0]
out=sys.argv[1]; items=sys.argv[2:]
os.makedirs(out,exist_ok=True)
srv=subprocess.Popen([sys.executable,'-m','http.server','8765'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); time.sleep(1)
try:
  with sync_playwright() as p:
    b=p.chromium.launch(executable_path=EXE,args=['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader','--ignore-gpu-blocklist'])
    pg=b.new_page(viewport={'width':2048,'height':2048})
    pg.on('console', lambda m: print('console:', m.text) if m.type in ('error','warning') else None)
    pg.on('pageerror', lambda e: print('pageerror:', e))
    for it in items:
      c,s=it.split('-'); t=time.time()
      pg.goto(f'http://localhost:8765/'+os.environ.get('PAGE','render.html')+f'?c={c}&s={s}&size=2048'); pg.wait_for_function('window.__done',timeout=300000)
      tmp=f'{out}/{it}@2x.png'; pg.locator('canvas').screenshot(path=tmp,omit_background=True)
      Image.open(tmp).resize((1024,1024),Image.LANCZOS).save(f'{out}/{it}.png'); os.remove(tmp)
      print(it, round(time.time()-t,1),'s')
    b.close()
finally: srv.terminate()

"""Optional browser integration test. Requires playwright + Chromium and g++.
Uses the actual C++ protocol over a Web Serial adapter; no physical USB claims.
Run from any directory: python tools/check_web_hardware.py.
"""
import asyncio,os,subprocess,time,json,pathlib,sys,tempfile,socket
from playwright.async_api import async_playwright
ROOT=pathlib.Path(__file__).resolve().parents[1]
OUT=ROOT/'results'/time.strftime('v3_browser_%Y%m%d_%H%M%S')
OUT.mkdir(parents=True,exist_ok=False)
SERVER=None
async def main():
 global SERVER
 with socket.socket() as check:
  if check.connect_ex(('127.0.0.1',8011))==0: raise RuntimeError('Port 8011 already in use')
 temporary=tempfile.TemporaryDirectory(prefix='traffic-browser-')
 binary=pathlib.Path(temporary.name)/'serial-simulator'
 subprocess.run(['g++','-std=c++11','-Wall','-Wextra','-Werror',str(ROOT/'hardware/simulator.cpp'),'-o',str(binary)],check=True)
 serverlog=(OUT/'browser_server.log').open('w')
 SERVER=subprocess.Popen([sys.executable,'main.py','--web','--no-browser','--web-port','8011'],cwd=ROOT,stdout=serverlog,stderr=subprocess.STDOUT)
 core=await asyncio.create_subprocess_exec(str(binary),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE)
 lock=asyncio.Lock(); start=time.monotonic(); transcript=[]
 async def core_line(line):
  core.stdin.write((line+'\n').encode());await core.stdin.drain(); output=[]
  while True:
   raw=(await core.stdout.readline()).decode().strip()
   if not raw: raise RuntimeError('Core exited')
   if raw.startswith('PINS,'):
    mask=int(raw.split(',')[1]); colors=[mask>>(3*i)&7 for i in range(4)]
    assert colors[0]==colors[1] and colors[2]==colors[3]
    assert not(colors[0]==4 and colors[2]==4)
    if mask&(1<<13):assert colors==[1,1,1,1]
    break
   output.append(raw);transcript.append(raw)
  return output
 async def write(source,text):
  async with lock:
   out=await core_line('@'+str(int((time.monotonic()-start)*1000)))
   for line in text.splitlines():out+=await core_line(line)
   return out
 mock='''
 window.serialPush = lines => { for(const line of lines) if(window.serialReader) serialReader.enqueue(new TextEncoder().encode(line+'\\n')); };
 Object.defineProperty(navigator,'serial',{value:{requestPort:async()=>({
   async open(){this.readable=new ReadableStream({start(c){window.serialReader=c;},cancel(){window.serialReader=null;}});
     this.writable=new WritableStream({async write(bytes){const lines=await window.coreWrite(new TextDecoder().decode(bytes)); serialPush(window.dropAck?lines.filter(x=>!x.startsWith('ACK,')):lines);}});},
   async close(){}
 })}});
 '''
 try:
  async with async_playwright() as pw:
   browser=await pw.chromium.launch(headless=True,args=['--no-sandbox'])
   page=await browser.new_page(viewport={'width':1440,'height':1000});errors=[]
   page.on('pageerror',lambda e:errors.append(str(e)))
   async def wait_expr(expr,timeout=30000):
    end=time.monotonic()+timeout/1000
    while time.monotonic()<end:
     if await page.evaluate(expr): return
     await asyncio.sleep(.1)
    raise AssertionError('Timeout: '+expr+'; '+str(await page.locator('#toast').text_content())+'; '+str(await page.locator('#hw-error').text_content()))
   await page.expose_binding('coreWrite',write);await page.add_init_script(mock)
   for _ in range(50):
    try:await page.goto('http://127.0.0.1:8011');break
    except Exception:await asyncio.sleep(.1)
   await wait_expr("typeof project!=='undefined' && !!project")
   # Ordinary simulator still works with no device.
   await page.click('#try-demo');await wait_expr('sessions.sim?.data.time>=1')
   await page.click('#sim-toolbar [data-control="pause"]')
   await page.select_option('#sim-controller','FIXED');await wait_expr("sessions.sim.data.views[0].controller==='FIXED'")
   await page.click('nav a[href="#hardware"]');await page.click('#hw-connect')
   await wait_expr('hardware.serial.connected',timeout=10000)
   await page.select_option('#hw-controller','FIXED');await page.click('#hw-bind')
   await wait_expr('hardware.active',timeout=10000)
   print('CONNECTED AND SYNCED',flush=True)
   # Navigate without stopping selected hardware, inject jams and a physical button.
   await page.click('nav a[href="#simulator"]')
   await page.click('#sim-toolbar [data-speed="100"]')
   assert await page.evaluate('sessions.sim.speed')==1
   await page.click('#simulator [data-jam="NS"]')
   async with lock:
    lines=await core_line('!1')
   await asyncio.sleep(.06)
   async with lock:lines+=await core_line('@'+str(int((time.monotonic()-start)*1000)))
   await page.evaluate('lines=>serialPush(lines)',lines)
   await wait_expr('sessions.sim.data.views[0].pedestrian_request')
   print('MANUAL JAM AND HARDWARE BUTTON',flush=True)
   await wait_expr("hardware.serial.phase==='PED_GREEN'",timeout=55000)
   assert await page.evaluate("Object.values(sessions.sim.data.views[0].signals).every(s=>s==='RED')")
   print('PED GREEN SAFE',flush=True)
   await wait_expr("hardware.serial.phase==='EW_GREEN'",timeout=25000)
   await page.click('nav a[href="#hardware"]');await page.screenshot(path=str(OUT/'hardware.png'),full_page=True)
   # An ACK loss must detach and never label an unconfirmed green as synchronized.
   await page.evaluate('window.dropAck=true')
   await wait_expr('!hardware.serial.connected',timeout=6000)
   assert 'DESYNC' in await page.locator('#hw-sync-status').inner_text()
   await asyncio.sleep(2.6)
   async with lock:await core_line('@'+str(int((time.monotonic()-start)*1000)))
   assert 'ERROR,TIMEOUT' in transcript
   await page.evaluate('window.dropAck=false')
   await page.click('#hw-connect');await wait_expr('hardware.serial.connected',timeout=10000)
   await page.click('#hw-bind');await wait_expr('hardware.active',timeout=10000)
   await page.click('nav a[href="#simulator"]');await page.click('#sim-toolbar [data-control="finish"]')
   await wait_expr('sessions.sim.data.finished')
   assert await page.evaluate("!hardware.active && hardware.serial.phase==='SAFE_ALL_RED'")
   # Compare four algorithms, then laboratory without an admin token on localhost.
   await page.click('nav a[href="#compare"]');await wait_expr('!!sessions.compare')
   await page.locator('#compare-controllers input').evaluate_all('(xs)=>xs.forEach(x=>x.checked=true)')
   await page.fill('#compare-duration','30');await page.locator('#compare-form button[type="submit"]').click()
   await wait_expr('sessions.compare.data.views.length===4')
   await page.click('nav a[href="#hardware"]');await page.select_option('#hw-source','compare');await page.select_option('#hw-controller','ADAPTIVE_V2')
   await page.click('#hw-bind');await wait_expr("hardware.active && hardware.controller==='ADAPTIVE_V2'",timeout=10000)
   await wait_expr('sessions.compare.data.time>=2')
   assert await page.evaluate("hardware.serial.phase===sessions.compare.data.views.find(v=>v.controller==='ADAPTIVE_V2').phase")
   await page.select_option('#hw-controller','FIXED');await wait_expr("!hardware.active && hardware.serial.phase==='SAFE_ALL_RED'")
   # Cancel a pending SYNC: no delayed task may re-open green afterwards.
   await page.click('#hw-bind');await wait_expr("hardware.status.startsWith('SYNC')")
   await page.click('#hw-stop');await asyncio.sleep(4.4)
   assert await page.evaluate("!hardware.active && hardware.serial.phase==='SAFE_ALL_RED'")
   await page.click('#hw-bind');await wait_expr("hardware.active && hardware.controller==='FIXED'",timeout=10000)
   await page.click('nav a[href="#compare"]')
   await page.click('#compare-toolbar [data-control="finish"]');await wait_expr('sessions.compare.data.finished')
   await page.click('nav a[href="#laboratory"]');await page.select_option('#lab-scenario','NS_HEAVY');await page.fill('#lab-runs','3');await page.fill('#lab-duration','30')
   await page.click('#run-lab');await wait_expr("job?.status==='complete'",timeout=20000)
   assert await page.evaluate('job.completed_runs')==12
   await page.click('nav a[href="#hardware"]');await page.click('#hw-disconnect')
   assert not errors,errors
   (OUT/'browser_audit.json').write_text(json.dumps({'status':'PASS','js_errors':errors,'transcript':transcript,'checks':['ordinary simulator','WebSerial transport with real C++ core','safe sync','x100 blocked','manual jam','debounced hardware button to Python','PED_GREEN safe','EW_GREEN protected','lost ACK and watchdog','reconnect','fast-forward ALL_RED','four-controller compare','selected V2/FIXED hardware output','cancel SYNC stays ALL_RED','laboratory12runs']},indent=2))
   print('BROWSER PASS',flush=True);await browser.close()
 finally:
  core.terminate();await core.wait();SERVER.terminate();SERVER.wait(timeout=15);serverlog.close();temporary.cleanup()
asyncio.run(main())

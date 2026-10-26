// Safety checks execute the shipped JS, no browser implementation is copied here.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const events={},elements=new Map();
global.window=global;
global.addEventListener=()=>{};
global.document={hidden:false,addEventListener:(n,fn)=>events[n]=fn,
  getElementById:id=> { if(!elements.has(id)) elements.set(id,{}); return elements.get(id); }};
vm.runInThisContext(fs.readFileSync('web/static/serial.js','utf8'));
(async()=> {
  const notices=[];
  const h=new HardwareDemo({notify:message=>notices.push(message)});
  let stops=0;
  h.serial.connected=true;
  h.serial.command=async verb=> { if(verb==='STOP'){stops++;h.serial.phase='SAFE_ALL_RED';} };
  try {
    // Hiding during the 4 s startup wait must invalidate the pending bind, too.
    h.binding=true; const epoch=h.epoch; document.hidden=true;
    events.visibilitychange(); await Promise.resolve();
    assert(h.epoch>epoch); assert.equal(stops,1); assert.equal(h.active,false);
    h.binding=false; document.hidden=false;
    // A stale HTTP result can never become a physical command.
    h.active=true;h.mode='sim';h.controller='FIXED';h.sessionId='s';h.revision=5;
    await h.snapshot('sim',{id:'s',revision:4,views:[]},performance.now());
    assert.equal(h.active,false);assert.equal(stops,2);assert.match(h.status,/DESYNC/);
    assert.equal(h.serial.phase,'SAFE_ALL_RED');
    // Skipped model seconds, even with a new revision, fail closed.
    h.active=true;h.modelTime=10;
    await h.snapshot('sim',{id:'s',revision:6,views:[{controller:'FIXED',time:12}]},performance.now());
    assert.equal(h.active,false);assert.equal(stops,3);
  } finally { clearInterval(h.timer); }
  const faults=[];
  const serial=new AdaptiveSerial(()=>{},()=>{},e=>faults.push(e));
  serial.connected=true;serial.pending={expected:'1',resolve:()=>assert.fail('Wrong ACK accepted'),reject:()=>{}};
  serial.receive('ACK,2,EW_GREEN');
  assert.equal(serial.connected,false);assert.equal(serial.phase,null);assert.equal(faults.length,1);
  console.log('Frontend safety: hidden startup, stale revision, skipped step, wrong ACK PASS');
})().catch(e=> { console.error(e); process.exitCode=1; });

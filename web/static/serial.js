'use strict';
// USB transport only: traffic decisions and timings come from Python.
const hardwarePhases = new Set(['SAFE_ALL_RED','NS_GREEN','NS_YELLOW','ALL_RED_TO_EW',
  'EW_GREEN','EW_YELLOW','ALL_RED_TO_NS','ALL_RED_TO_PED','PED_GREEN','PED_CLEARANCE','ALL_RED_AFTER_PED']);
const hardwareSleep = ms => new Promise(resolve => setTimeout(resolve, ms));

class AdaptiveSerial {
  constructor(onChange, onButton, onFault) {
    this.onChange=onChange; this.onButton=onButton; this.onFault=onFault;
    this.connected=false; this.sequence=0; this.lastCommand='—'; this.lastAck='—';
    this.phase=null; this.error=''; this.pending=null; this.queue=Promise.resolve();
  }
  async connect() {
    if (!window.isSecureContext || !navigator.serial) throw Error('Нужен Chrome/Chromium: HTTPS или localhost, с Web Serial.');
    if (this.port) throw Error('Порт уже открыт или открывается');
    // Must remain a direct consequence of the user's Connect click.
    const port=await navigator.serial.requestPort();
    this.port=port;
    try {
      await port.open({baudRate:115200,bufferSize:256});
      this.writer=port.writable.getWriter();
      this.reader=port.readable.getReader();
      this.readTask=this.readLoop();
      await hardwareSleep(1800); // Uno normally resets on open; async, never blocks UI.
      this.sequence=0;
      await this.exchange('HELLO','READY');
      this.connected=true; this.error=''; this.phase='SAFE_ALL_RED'; this.onChange();
    } catch(e) { await this.close(); throw e; }
  }
  async readLoop() {
    const decoder=new TextDecoder(); let buffer='';
    try {
      while (this.reader) {
        const {value,done}=await this.reader.read();
        if (done) break;
        buffer+=decoder.decode(value,{stream:true});
        if (buffer.length>512) throw Error('Слишком длинный ответ Serial');
        let end;
        while ((end=buffer.indexOf('\n'))>=0) {
          const line=buffer.slice(0,end).replace(/\r$/,''); buffer=buffer.slice(end+1);
          this.receive(line);
        }
      }
      if (!this.closing) this.fail('USB отключён или поток Serial закрыт');
    } catch(e) { if (!this.closing) this.fail(e.message); }
  }
  receive(line) {
    if (line==='BUTTON,PED') { if (this.connected) this.onButton(); return; }
    if (line.startsWith('ERROR,')) { this.fail(`Arduino: ${line}`,true); return; }
    const pending=this.pending;
    if (line.startsWith('READY,')) {
      if (!pending || pending.expected!=='READY' || line!=='READY,ADAPTIVETRAFFIC,1,3000,1000,10000,3000') {
        this.fail('Устройство перезапущено или версия/интервалы прошивки не совпадают'); return;
      }
    } else {
      const parts=line.split(',');
      if (parts.length!==3 || parts[0]!=='ACK' || !hardwarePhases.has(parts[2])) { this.fail('Некорректный ответ Serial'); return; }
      if (!pending || parts[1]!==pending.expected) {
        if (line===this.lastAck) return; // late duplicate of the last confirmation
        this.fail('Нарушен порядок подтверждений'); return;
      }
      this.phase=parts[2];
    }
    this.lastAck=line; this.onChange(); pending.resolve(line);
  }
  async exchange(line, expected) {
    if (this.pending) throw Error('Serial-команда уже выполняется');
    this.lastCommand=line; this.onChange();
    let timer;
    const response=new Promise((resolve,reject)=> { this.pending={expected,resolve,reject}; });
    // Attach rejection handler before USB write to avoid unhandled rejection on disconnect.
    response.catch(()=>{});
    try {
      const timeout=new Promise((_,reject)=> {
        timer=setTimeout(()=>reject(Error('Нет ACK за 1600 мс')),1600);
      });
      const writeAndRead=(async()=> {
        await this.writer.write(new TextEncoder().encode(line+'\n'));
        return response;
      })();
      // One retry of exactly the same command/sequence; device caches the last ACK.
      const retry=setTimeout(()=> {
        if (this.pending && this.writer) this.writer.write(new TextEncoder().encode(line+'\n')).catch(e=>this.fail(e.message));
      },800);
      try { return await Promise.race([writeAndRead,timeout]); }
      finally { clearTimeout(retry); }
    } finally { clearTimeout(timer); this.pending=null; }
  }
  command(verb, ...args) {
    const work=this.queue.then(async()=> {
      if (!this.connected) throw Error('Arduino не подключена');
      return this.exchange([verb,++this.sequence,...args].join(','),String(this.sequence));
    });
    this.queue=work.catch(()=>{});
    return work.catch(e=> { this.fail(e.message); throw e; });
  }
  fail(message, confirmedSafe=false) {
    this.error=message; this.connected=false;
    this.phase=confirmedSafe?'SAFE_ALL_RED':null;
    if (this.pending) this.pending.reject(Error(message));
    this.onFault(message); this.onChange();
    // No more heartbeat. A powered board independently closes all greens after 2500 ms.
    this.close().catch(()=>{});
  }
  async close() {
    if (this.closing) return;
    this.closing=true; this.connected=false;
    if (this.pending) this.pending.reject(Error('Serial закрыт'));
    try {
      if (this.reader) { await this.reader.cancel().catch(()=>{}); await this.readTask; this.reader.releaseLock(); this.reader=null; }
      if (this.writer) { await this.writer.abort().catch(()=>{}); this.writer.releaseLock(); this.writer=null; }
      if (this.port) await this.port.close().catch(()=>{});
    } finally { this.port=null; this.closing=false; this.onChange(); }
  }
}

class HardwareDemo {
  constructor(hooks) {
    this.hooks=hooks; this.mode=null; this.controller=null; this.active=false;
    this.epoch=0; this.status='DISCONNECTED'; this.nextStep=0; this.lastFrame=0; this.binding=false;
    this.serial=new AdaptiveSerial(()=>this.render(),()=>this.button(),message=> {
      this.active=false; this.status='DESYNC — требуется подключение и синхронизация';
      this.hooks.notify(message);
    });
    this.timer=setInterval(()=>this.heartbeat(),500);
    document.addEventListener('visibilitychange',()=> {
      if (document.hidden && (this.active || this.binding)) this.suspend('Фоновая вкладка — вывод приостановлен').catch(()=>{});
    });
    window.addEventListener('pagehide',()=> { this.active=false; clearInterval(this.timer); this.serial.close(); });
    this.render();
  }
  render() {
    const put=(id,text)=> { const el=document.getElementById(id); if (el) el.textContent=text; };
    put('hw-connected',this.serial.connected?'CONNECTED':'DISCONNECTED');
    put('hw-sync-status',this.status); put('hw-phase',this.serial.phase||'UNKNOWN');
    put('hw-last-command',this.serial.lastCommand); put('hw-last-ack',this.serial.lastAck);
    put('hw-error',this.serial.error||'—');
    for (const d of ['NORTH','SOUTH','EAST','WEST']) {
      const p=this.serial.phase;
      const group=['NORTH','SOUTH'].includes(d)?'NS':'EW';
      put('hw-'+d, !p?'UNKNOWN':p===group+'_GREEN'?'GREEN':p===group+'_YELLOW'?'YELLOW':'RED');
    }
    put('hw-ped',!this.serial.phase?'UNKNOWN':this.serial.phase==='PED_GREEN'?'GREEN':'RED');
    put('hw-global',this.active?`USB ×1 · ${this.controller} · ${this.status}`:this.serial.connected?`USB · ${this.status}`:'USB: не подключена');
  }
  async heartbeat() {
    if (!this.serial.connected || this.serial.pending) return;
    if (this.active && performance.now()-this.lastFrame>3000) {
      await this.suspend('DESYNC — нет свежего ответа Python').catch(()=>{}); return;
    }
    try { await this.serial.command('PING'); } catch { /* fail() displays the fault */ }
  }
  async suspend(reason='SAFE — аппаратный вывод приостановлен') {
    this.epoch++; this.active=false; this.status=reason; this.render();
    if (this.serial.connected) await this.serial.command('STOP');
    this.render();
  }
  async disconnect() {
    try { await this.suspend('SAFE — отключено'); } finally { await this.serial.close(); }
  }
  async bind(mode, controller) {
    if (this.binding) throw Error('Синхронизация уже выполняется');
    if (!this.serial.connected) throw Error('Сначала подключите Arduino');
    this.binding=true;
    try {
      await this.suspend();
      const epoch=this.epoch;
      this.hooks.pause(mode);
      await this.hooks.settled(mode);
      const data=await this.hooks.restart(mode);
      if (!data.views.some(v=>v.controller===controller)) throw Error('Выберите контроллер, присутствующий в этой симуляции');
      this.mode=mode; this.controller=controller; this.sessionId=data.id; this.revision=-1; this.modelTime=-1;
      if (epoch!==this.epoch) throw Error('Синхронизация отменена');
      this.status='SYNC — ALL_RED, 4 секунды'; this.render();
      await this.serial.command('SYNC');
      await hardwareSleep(4100);
      if (epoch!==this.epoch) throw Error('Синхронизация отменена');
      const current=await this.hooks.current(mode);
      if (current.id!==this.sessionId || current.revision!==data.revision || current.time!==0) throw Error('Модель изменилась во время синхронизации');
      await this.frame(current,performance.now());
      if (epoch!==this.epoch) throw Error('Синхронизация отменена');
      this.active=true; this.hooks.start(mode); this.render();
    } catch(e) { await this.suspend('DESYNC — '+e.message).catch(()=>{}); throw e; }
    finally { this.binding=false; }
  }
  async frame(data, started) {
    const epoch=this.epoch;
    if (performance.now()-started>1500 || data.id!==this.sessionId || data.revision<=this.revision) throw Error('Устаревший снимок Python; вывод остановлен');
    const view=data.views.find(v=>v.controller===this.controller);
    if (!view || view.time<this.modelTime || (this.modelTime>=0 && view.time-this.modelTime>1)) throw Error('Пропущен шаг выбранной модели');
    this.status='SENDING — ожидаем ACK'; this.render();
    await this.serial.command('STATE',Math.round(view.time*1000),view.phase);
    if (this.serial.phase!==view.phase) throw Error('Arduino подтвердила другое состояние');
    if (epoch!==this.epoch) return;
    this.revision=data.revision; this.modelTime=view.time;
    this.nextStep=performance.now()+1010; this.lastFrame=performance.now();
    this.status='IN_SYNC — подтверждено Arduino'; this.render();
  }
  async snapshot(mode,data,started) {
    if (!this.active || mode!==this.mode) return;
    try {
      await this.frame(data,started);
      if (data.finished || data.views.find(v=>v.controller===this.controller).finished) await this.suspend('SAFE — выбранная модель завершена');
    } catch(e) { await this.suspend('DESYNC — '+e.message).catch(()=>{}); this.hooks.notify(e.message); }
  }
  canStep(mode) { return this.active && this.mode===mode && performance.now()>=this.nextStep && !this.serial.pending; }
  async button() {
    if (!this.active || this.buttonPending) return;
    this.buttonPending=true;
    try { await this.hooks.pedestrian(this.mode); }
    catch(e) { await this.suspend('DESYNC — пешеходный запрос не доставлен').catch(()=>{}); this.hooks.notify(e.message); }
    finally { this.buttonPending=false; }
  }
}
window.AdaptiveSerial=AdaptiveSerial;
window.HardwareDemo=HardwareDemo;

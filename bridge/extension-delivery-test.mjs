// Execute the actual background service-worker source with a synthetic Chrome API.
// No browser login, website access, or ChatGPT output is required.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { webcrypto } from 'node:crypto';
import { fileURLToPath } from 'node:url';

const source = readFileSync(path.resolve(
  path.dirname(fileURLToPath(import.meta.url)), '../browser-extension/background.js'
), 'utf8');
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(predicate) {
  for (let i=0; i<300; i++) {
    const value=predicate();
    if (value) return value;
    await wait(5);
  }
  throw new Error('Timed out waiting for test event');
}

async function harness(saved={}, options={}) {
  const writes=[];
  const tabs=[
    {id:7,url:'https://chatgpt.com/c/synthetic-a',status:'complete',
      autoDiscardable:false,windowId:1,index:0,title:'Synthetic A'},
    {id:8,url:'https://chatgpt.com/c/synthetic-b',status:'complete',
      autoDiscardable:false,windowId:1,index:1,title:'Synthetic B'}
  ];
  const submissions=[];
  const removed=[];
  const sockets=[];
  class FakeWebSocket {
    static OPEN=1;
    static CONNECTING=0;
    constructor() {
      this.readyState=0;
      this.messages=[];
      sockets.push(this);
      setImmediate(()=>{this.readyState=1;this.onopen?.()});
    }
    send(text) {
      const msg=JSON.parse(text);
      this.messages.push(msg);
      if(msg.type==='hello') queueMicrotask(()=>
        this.receive({type:'hello-ack',ok:true,protocolMin:5,protocolMax:5}));
    }
    receive(payload) {
      return this.onmessage?.({data:JSON.stringify(payload)});
    }
    close() {this.readyState=3;this.onclose?.();}
  }
  const event={addListener(){}};
  const chrome={
    alarms:{create(){},onAlarm:event},
    runtime:{getManifest(){return {version:'0.6.0'}},onInstalled:event,onStartup:event},
    storage:{
      local:{
        async get(keys) {
          if(options.unreadable && keys.includes('wasCommandResultsV1')) {
            throw new Error('inaccessible command ledger');
          }
          return structuredClone(saved);
        },
        async set(change) {
          writes.push(structuredClone(change));
          if(options.writeFails)throw new Error('storage is full');
          Object.assign(saved,structuredClone(change));
        },
      },
      onChanged:event
    },
    tabs:{
      async query(){return tabs.map(t=>({...t}));},
      async get(id){const t=tabs.find(t=>t.id===id);if(!t)throw new Error('no tab');return {...t};},
      async update(id,change){const tab=tabs.find(t=>t.id===id);Object.assign(tab,change);return {...tab};},
      async reload(){},
      async remove(id){
        removed.push(id);
        const index=tabs.findIndex(t=>t.id===id);
        if(index>=0)tabs.splice(index,1);
      },
      async sendMessage(id,msg){
        if(msg.type==='send-chatgpt-message'){
          assert.equal(msg.expectedUrl,tabs.find(t=>t.id===id).url,
            'send target was not pinned to an exact conversation URL');
          submissions.push({tabId:id,text:msg.text});
          if(options.sendGate) await options.sendGate;
          return {ok:true};
        }
        return {ok:true,snapshot:{messages:[]}};
      },
      onCreated:event,onUpdated:event,onRemoved:event,onActivated:event
    },
    scripting:{async executeScript(){}}
  };
  const context=vm.createContext({
    chrome,WebSocket:FakeWebSocket,crypto:webcrypto,TextEncoder,
    URL,Date,JSON,Map,Set,Promise,Number,String,Array,Object,
    setTimeout,clearTimeout,setInterval:()=>0,clearInterval:()=>{}
  });
  vm.runInContext(source,context,{filename:'background.js'});
  const socket=await until(()=>sockets.find(s=>s.messages.some(m=>m.type==='hello')));
  await until(()=>socket.messages.some(m=>m.type==='hello') && socket.readyState===1);
  await wait(8);
  const reply=id=>socket.messages.find(m=>m.type==='send-result'&&m.id===id);
  return {socket,saved,writes,submissions,removed,reply,tabs};
}

async function main(){
  let release;
  const gate=new Promise(resolve=>{release=resolve});
  const saved={bridgeToken:'x'.repeat(64)};
  const a=await harness(saved,{sendGate:gate});
  const payload=(id,commandId,tabId=7,text='Original instruction')=>
    ({type:'send',id,commandId,tabId,text});
  const p1=a.socket.receive(payload('first','command-A'));
  const p2=a.socket.receive(payload('same','command-A'));
  const p3=a.socket.receive(payload('conflict','command-A',8,'Other instruction'));
  await until(()=>a.submissions.length===1);
  assert.ok(a.saved.wasCommandResultsV1?.some(e=>
    e.commandId==='command-A'&&e.result.uncertain));
  release();
  await Promise.all([p1,p2,p3]);
  assert.equal(a.submissions.length,1,'duplicate submitted more than once');
  assert.equal(a.reply('first').ok,true);
  assert.equal(a.reply('same').duplicate,true);
  assert.equal(a.reply('conflict').ok,false);
  assert.equal(a.reply('conflict').mayHaveExecuted,true);
  assert.match(a.reply('conflict').error,/another tab or message/);

  await a.socket.receive(payload('later','command-A',8));
  assert.equal(a.reply('later').ok,false,'reused ID redirected to another tab');
  await a.socket.receive({
    ...payload('wrong-url','expected-url-conflict'),
    expectedUrl:'https://chatgpt.com/c/not-the-current-conversation'
  });
  assert.equal(a.reply('wrong-url').ok,false,'expected URL mismatch was accepted');
  assert.equal(a.submissions.length,1,'wrong conversation received a send');
  await a.socket.receive({
    ...payload('expected-new','command-A'),
    expectedUrl:'https://chatgpt.com/c/synthetic-a'
  });
  assert.equal(a.reply('expected-new').ok,false,'command ID was reused for a different URL');
  await a.socket.receive({type:'send',id:'no-tab',commandId:'no-tab',text:'instruction'});
  assert.equal(a.reply('no-tab').ok,false);
  assert.equal(a.submissions.length,1);

  const b=await harness(saved);
  await b.socket.receive(payload('replayed','command-A'));
  assert.equal(b.reply('replayed').ok,true);
  assert.equal(b.reply('replayed').duplicate,true);
  assert.equal(b.submissions.length,0,'restart replay submitted again');
  await b.socket.receive(payload('legacy','old-v1',7));
  assert.equal(b.reply('legacy').ok,true);

  let unblock;
  const interruptedGate=new Promise(resolve=>{unblock=resolve});
  const c=await harness(saved,{sendGate:interruptedGate});
  const suspended=c.socket.receive(payload('interrupted','command-interrupted'));
  await until(()=>c.submissions.length===1 &&
    c.saved.wasCommandResultsV1?.some(e=>e.commandId==='command-interrupted'&&e.result.uncertain));
  const d=await harness(saved);
  await d.socket.receive(payload('after-crash','command-interrupted'));
  assert.equal(d.reply('after-crash').ok,false,'restarted worker must preserve uncertainty');
  assert.equal(d.reply('after-crash').duplicate,true);
  assert.equal(d.reply('after-crash').mayHaveExecuted,true);
  assert.equal(d.submissions.length,0,'unknown send replayed after restart');
  unblock();
  await suspended;

  const tabGuard=await harness({bridgeToken:'x'.repeat(64)});
  await tabGuard.socket.receive({
    type:'close-tab',id:'wrong-close',tabId:7,
    expectedUrl:'https://chatgpt.com/c/another-conversation'
  });
  const deniedClose=tabGuard.socket.messages.find(
    msg=>msg.type==='close-result'&&msg.id==='wrong-close');
  assert.equal(deniedClose.ok,false);
  assert.equal(tabGuard.removed.length,0,'wrong conversation was closed');
  await tabGuard.socket.receive({
    type:'close-tab',id:'right-close',tabId:7,
    expectedUrl:'https://chatgpt.com/c/synthetic-a'
  });
  const validClose=tabGuard.socket.messages.find(
    msg=>msg.type==='close-result'&&msg.id==='right-close');
  assert.equal(validClose.ok,true);
  assert.deepEqual(tabGuard.removed,[7]);

  const e=await harness({bridgeToken:'x'.repeat(64)},{unreadable:true});
  await e.socket.receive(payload('unreadable','unreadable'));
  assert.equal(e.reply('unreadable').ok,false);
  assert.equal(e.submissions.length,0,'unreadable cache must fail closed');

  const f=await harness({bridgeToken:'x'.repeat(64)},{writeFails:true});
  await f.socket.receive(payload('no-persist','fail-persist'));
  assert.equal(f.reply('no-persist').ok,false);
  assert.equal(f.submissions.length,0,'failed intent persistence cannot submit');
  await f.socket.receive(payload('retry-persist','fail-persist'));
  assert.equal(f.submissions.length,0,'failed cache write must reserve ID in memory');
  assert.equal(f.reply('retry-persist').duplicate,true);
  console.log('PASS: deduplication, recipient and close guards, restart replay, crash uncertainty, storage failure');
}
main().catch(error=>{console.error('FAIL:',error);process.exitCode=1});

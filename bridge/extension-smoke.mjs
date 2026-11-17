// Isolated Chromium extension/bridge smoke test; never visits ChatGPT.
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { once } from 'node:events';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import WebSocket from 'ws';

const here = path.dirname(fileURLToPath(import.meta.url));
const extension = path.resolve(here, '..', 'browser-extension');
const edge = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const wait = ms => new Promise(done => setTimeout(done, ms));
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'was-edge-isolated-'));
let browser, bridge, client;

async function freePort() {
  const s = net.createServer().listen(0, '127.0.0.1');
  await once(s, 'listening');
  const port = s.address().port;
  await new Promise(resolve => s.close(resolve));
  return port;
}
async function until(fn, timeout = 14000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    try { const value = await fn(); if (value) return value; } catch {}
    await wait(180);
  }
  throw new Error('Timed out waiting for isolated extension/bridge');
}
function cdp(ws) {
  let id = 0;
  const pending = new Map();
  ws.on('message', raw => {
    const msg = JSON.parse(raw.toString());
    if (!pending.has(msg.id)) return;
    const {resolve,reject} = pending.get(msg.id);
    pending.delete(msg.id);
    msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result);
  });
  return async (method, params={}) => new Promise((resolve, reject) => {
    const next = ++id;
    pending.set(next, {resolve,reject});
    ws.send(JSON.stringify({id: next,method,params}));
  });
}
function expectEval(result) {
  if (result.exceptionDetails) throw new Error('Extension execution error: ' +
    result.exceptionDetails.text);
  return result.result?.value;
}
try {
  assert.ok(fs.existsSync(edge), 'Microsoft Edge is not installed');
  const token = crypto.randomBytes(32).toString('hex');
  const port = await freePort();
  bridge = spawn(process.execPath,[path.join(here,'server.js')],{
    cwd:here,windowsHide:true,stdio:'ignore',
    env:{...process.env,WEB_SWARM_BIND:'127.0.0.1',
      WEB_SWARM_PORT:String(port), WEB_SWARM_BRIDGE_TOKEN:token},
  });
  browser = spawn(edge,[
    '--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check',
    '--disable-background-networking','--disable-sync',
    '--remote-debugging-port=0', '--user-data-dir='+profile,
    '--disable-extensions-except='+extension, '--load-extension='+extension,
    'about:blank'
  ],{windowsHide:true,stdio:'ignore'});

  const debuggerPort = await until(async () => {
    const file=path.join(profile,'DevToolsActivePort');
    if(!fs.existsSync(file))return null;
    return Number(fs.readFileSync(file,'utf8').split(/\r?\n/)[0]);
  });
  const worker = await until(async () => {
    const response=await fetch('http://127.0.0.1:'+debuggerPort+'/json/list');
    const tabs=await response.json();
    for (const candidate of tabs.filter(t=>t.type==='service_worker' &&
      t.url?.startsWith('chrome-extension://') &&
      t.url?.endsWith('/background.js'))) {
      let probe;
      try {
        probe=new WebSocket(candidate.webSocketDebuggerUrl);
        await once(probe,'open');
        const evaluate=cdp(probe);
        await evaluate('Runtime.enable');
        const manifest=expectEval(await evaluate('Runtime.evaluate',{
          expression:'chrome.runtime.getManifest().name',returnByValue:true
        }));
        if(manifest==='Web Agent Swarm Bridge') return {probe,evaluate};
      } catch {}
      probe?.terminate();
    }
    return null;
  });
  client=worker.probe;
  const send=worker.evaluate;
  const alarm = expectEval(await send('Runtime.evaluate',{
    expression:"chrome.alarms.get('wasBridgeReconnect').then(a=>Boolean(a && a.periodInMinutes===0.5))",
    awaitPromise:true,returnByValue:true
  }));
  assert.equal(alarm,true,'reconnect alarm was not installed');

  const configured = expectEval(await send('Runtime.evaluate',{
    expression:'chrome.storage.local.set('+JSON.stringify({
      bridgeWsUrl:'ws://127.0.0.1:'+port+'/ws',bridgeToken:token
    })+').then(()=>true)',awaitPromise:true,returnByValue:true
  }));
  assert.equal(configured,true);
  const auth={'X-WAS-Token':token};
  const ready = await until(async ()=>{
    const res=await fetch('http://127.0.0.1:'+port+'/health',{headers:auth});
    if(!res.ok)return false;
    return (await res.json()).extensionClients===1;
  },22000);
  assert.ok(ready,'extension did not connect to loopback bridge');
  const tabs = await (await fetch('http://127.0.0.1:'+port+'/tabs',
    {headers:auth})).json();
  assert.equal(tabs.ok,true);
  assert.deepEqual(tabs.tabs,[],'isolated profile unexpectedly exposed tabs');

  bridge.kill();
  await Promise.race([once(bridge,'exit'),wait(5000).then(()=>{throw new Error('bridge did not stop');})]);
  bridge = spawn(process.execPath,[path.join(here,'server.js')],{
    cwd:here,windowsHide:true,stdio:'ignore',
    env:{...process.env,WEB_SWARM_BIND:'127.0.0.1',
      WEB_SWARM_PORT:String(port), WEB_SWARM_BRIDGE_TOKEN:token},
  });
  const recovered = await until(async ()=>{
    const health=await fetch('http://127.0.0.1:'+port+'/health',{headers:auth});
    return health.ok && (await health.json()).extensionClients===1;
  },22000);
  assert.ok(recovered,'extension did not reconnect after bridge restart');
  console.log('PASS: isolated Edge extension load, MV3 service worker, reconnect alarm, authenticated bridge, zero unrelated tabs, restart/reconnect');
} catch(error) {
  console.error('FAIL:',error.message);
  process.exitCode=1;
} finally {
  client?.terminate();
  bridge?.kill();
  if(browser?.pid) spawnSync('taskkill',['/PID',String(browser.pid),'/T','/F'],
    {windowsHide:true,stdio:'ignore',timeout:5000});
  await wait(350);
  try {fs.rmSync(profile,{recursive:true,force:true,maxRetries:3,retryDelay:250});} catch{}
}

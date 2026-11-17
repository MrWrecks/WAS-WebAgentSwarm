// Local-only bridge round-trip. No ChatGPT tab, browser profile or user data is used.
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import net from 'node:net';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import WebSocket from 'ws';

const here = path.dirname(fileURLToPath(import.meta.url));
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

async function freePort() {
  const listener = net.createServer();
  listener.listen(0, '127.0.0.1');
  await once(listener, 'listening');
  const port = listener.address().port;
  await new Promise((resolve, reject) =>
    listener.close(error => error ? reject(error) : resolve()));
  return port;
}

function nextMessage(ws, expectedType) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      ws.off('message', onMessage);
      reject(new Error('Timed out waiting for ' + expectedType));
    }, 5000);
    const onMessage = raw => {
      const msg = JSON.parse(raw.toString());
      if (msg.type !== expectedType) return;
      clearTimeout(timer);
      ws.off('message', onMessage);
      resolve(msg);
    };
    ws.on('message', onMessage);
  });
}

async function main() {
  const token = crypto.randomBytes(32).toString('hex');
  const port = await freePort();
  const url = 'http://127.0.0.1:' + port;
  const child = spawn(process.execPath, [path.join(here, 'server.js')], {
    cwd: here,
    env: {
      ...process.env,
      WEB_SWARM_BIND: '127.0.0.1',
      WEB_SWARM_PORT: String(port),
      WEB_SWARM_BRIDGE_TOKEN: token,
    },
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let stderr = '';
  child.stderr.on('data', data => { stderr += data.toString().slice(0, 2000); });
  let ws;
  const auth = { 'X-WAS-Token': token };
  try {
    let ready = false;
    for (let attempt = 0; attempt < 50; attempt++) {
      if (child.exitCode !== null) throw new Error('Bridge exited: ' + stderr);
      try {
        const health = await fetch(url + '/health', { headers: auth });
        if (health.ok) { ready = true; break; }
      } catch {}
      await pause(100);
    }
    assert.ok(ready, 'bridge did not start on loopback');
    const anonymous = await fetch(url + '/health');
    assert.equal(anonymous.status, 401, 'unauthenticated call was accepted');

    ws = new WebSocket('ws://127.0.0.1:' + port + '/ws?token=' + token, {
      origin: 'chrome-extension://syntheticlocaltest',
    });
    await once(ws, 'open');
    ws.on('message', raw => {
      const msg = JSON.parse(raw.toString());
      if (msg.type === 'list-tabs') {
        ws.send(JSON.stringify({
          type: 'tabs-result', id: msg.id, tabs: [{
            id: 42, title: 'Synthetic chat tab',
            url: 'https://chatgpt.com/c/only-a-test'
          }]
        }));
      }
      if (msg.type === 'send') {
        ws.send(JSON.stringify({
          type: 'send-result', id: msg.id, ok: true,
          commandId: msg.commandId, tabId: msg.tabId,
        }));
      }
    });
    const handshake = nextMessage(ws, 'hello-ack');
    ws.send(JSON.stringify({ type: 'hello', protocolMin: 5, protocolMax: 5,
      extensionVersion: 'synthetic-test' }));
    assert.equal((await handshake).ok, true);

    const health = await (await fetch(url + '/health', { headers: auth })).json();
    assert.equal(health.extensionClients, 1);

    const tabs = await (await fetch(url + '/tabs', { headers: auth })).json();
    assert.equal(tabs.tabs.length, 1);
    assert.equal(tabs.tabs[0].id, 42);

    const sent = await fetch(url + '/send', {
      method: 'POST',
      headers: { ...auth, 'Content-Type': 'application/json' },
      body: JSON.stringify({ tabId: 42, commandId: 'synthetic-unique-1',
        text: 'Synthetic message; no browser is involved' }),
    });
    assert.equal(sent.status, 200);
    const result = await sent.json();
    assert.equal(result.ok, true);
    assert.equal(result.commandId, 'synthetic-unique-1');

    const missingId = await fetch(url + '/send', {
      method: 'POST',
      headers: { ...auth, 'Content-Type': 'application/json' },
      body: JSON.stringify({ tabId: 42, text: 'invalid untracked send' }),
    });
    assert.equal(missingId.status, 400);
    console.log('PASS: loopback auth, WebSocket handshake, tab routing, send ack, command ID gate');
  } finally {
    ws?.terminate();
    child.kill();
    await Promise.race([once(child, 'exit'), pause(2000)]);
  }
}

main().catch(error => {
  console.error('FAIL:', error);
  process.exitCode = 1;
});

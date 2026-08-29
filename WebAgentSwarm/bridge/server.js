import http from 'node:http';
import crypto from 'node:crypto';
import { WebSocketServer, WebSocket } from 'ws';

const HOST = process.env.WEB_SWARM_BIND || '127.0.0.1';
const PORT = Number.parseInt(process.env.WEB_SWARM_PORT || '8788', 10);
const clients = new Set();
const pending = new Map();

function json(res, status, body) {
  if (res.writableEnded) return;
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store'
  });
  res.end(JSON.stringify(body));
}

function broadcast(message) {
  let sent = 0;
  for (const ws of clients) {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(message);
      sent += 1;
    }
  }
  return sent;
}

function awaitResult(id, type, res, timeoutMs = 5000) {
  const timer = setTimeout(() => {
    const item = pending.get(id);
    if (!item) return;
    pending.delete(id);
    json(res, 504, { ok: false, error: 'Timed out waiting for extension response.' });
  }, timeoutMs);
  pending.set(id, { type, res, timer });
}

function ensureClient(res) {
  if (clients.size > 0) return true;
  json(res, 503, { ok: false, error: 'Browser extension is not connected.' });
  return false;
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url || '/', `http://${HOST}:${PORT}`);

  if (req.method === 'GET' && url.pathname === '/health') {
    return json(res, 200, {
      ok: true,
      extensionClients: clients.size,
      protocolVersion: 1
    });
  }

  if (req.method === 'GET' && url.pathname === '/tabs') {
    if (!ensureClient(res)) return;
    const id = crypto.randomUUID();
    awaitResult(id, 'tabs-result', res);
    broadcast(JSON.stringify({ type: 'list-tabs', id }));
    return;
  }

  if (req.method === 'GET' && url.pathname === '/inspect') {
    if (!ensureClient(res)) return;
    const tabId = Number.parseInt(url.searchParams.get('tabId') || '', 10);
    if (!Number.isInteger(tabId)) {
      return json(res, 400, { ok: false, error: 'tabId is required.' });
    }
    const tail = Math.max(1, Math.min(
      Number.parseInt(url.searchParams.get('tail') || '6', 10) || 6,
      20
    ));
    const id = crypto.randomUUID();
    awaitResult(id, 'inspect-result', res, 6000);
    broadcast(JSON.stringify({ type: 'inspect', id, tabId, tail }));
    return;
  }

  if (req.method === 'POST' && (url.pathname === '/open' || url.pathname === '/send')) {
    let body = '';
    req.setEncoding('utf8');
    req.on('data', chunk => {
      body += chunk;
      if (body.length > 200000) req.destroy();
    });
    req.on('end', () => {
      let data;
      try {
        data = JSON.parse(body || '{}');
      } catch {
        return json(res, 400, { ok: false, error: 'Invalid JSON.' });
      }
      if (!ensureClient(res)) return;

      const id = crypto.randomUUID();
      if (url.pathname === '/open') {
        const target = String(data.url || 'https://chatgpt.com/').trim();
        if (!target.startsWith('https://chatgpt.com/')) {
          return json(res, 400, { ok: false, error: 'Only chatgpt.com URLs may be opened.' });
        }
        awaitResult(id, 'open-result', res);
        broadcast(JSON.stringify({
          type: 'open-tab',
          id,
          url: target,
          active: Boolean(data.active)
        }));
        return;
      }

      const text = String(data.text || '').trim();
      if (!text) return json(res, 400, { ok: false, error: 'text is required.' });
      const tabId = Number.isInteger(data.tabId) ? data.tabId : undefined;
      awaitResult(id, 'send-result', res, 8000);
      broadcast(JSON.stringify({ type: 'send', id, tabId, text }));
    });
    return;
  }

  json(res, 404, { ok: false, error: 'Not found.' });
});

const wss = new WebSocketServer({
  server,
  path: '/ws',
  verifyClient(info, done) {
    const origin = info.origin || '';
    const allowed = !origin || origin.startsWith('chrome-extension://');
    done(allowed, allowed ? 200 : 403, allowed ? 'OK' : 'Forbidden');
  }
});

wss.on('connection', ws => {
  clients.add(ws);

  ws.on('message', raw => {
    try {
      const msg = JSON.parse(raw.toString());
      if (msg.type === 'ping') return;
      const item = pending.get(msg.id);
      if (!item || item.type !== msg.type) return;
      clearTimeout(item.timer);
      pending.delete(msg.id);
      if (msg.type === 'tabs-result') {
        json(item.res, 200, { ok: true, tabs: msg.tabs || [] });
      } else {
        json(item.res, msg.ok ? 200 : 502, msg);
      }
    } catch {}
  });

  ws.on('close', () => clients.delete(ws));
});

server.listen(PORT, HOST, () => {
  console.error(`Web Agent Swarm bridge listening on http://${HOST}:${PORT}`);
});

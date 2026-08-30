import http from 'node:http';
import crypto from 'node:crypto';
import { WebSocketServer, WebSocket } from 'ws';

const HOST = process.env.WEB_SWARM_BIND || '127.0.0.1';
const PORT = Number.parseInt(process.env.WEB_SWARM_PORT || '8788', 10);
const clients = new Set();
const pending = new Map();
const browserEvents = [];
const eventWaiters = new Set();
const MAX_BROWSER_EVENTS = 512;
let browserEventSeq = 0;

function browserEventsSince(since = 0, limit = 100) {
  const cleanSince = Math.max(0, Number.parseInt(String(since || 0), 10) || 0);
  const cleanLimit = Math.max(1, Math.min(Number.parseInt(String(limit || 100), 10) || 100, 512));
  return browserEvents.filter(event => event.seq > cleanSince).slice(0, cleanLimit);
}

function flushEventWaiters() {
  for (const waiter of [...eventWaiters]) {
    const events = browserEventsSince(waiter.since, waiter.limit);
    if (!events.length) continue;
    clearTimeout(waiter.timer);
    eventWaiters.delete(waiter);
    json(waiter.res, 200, {
      ok: true,
      events,
      latestSeq: browserEventSeq
    });
  }
}

function appendBrowserEvent(kind, payload = {}) {
  const event = {
    seq: ++browserEventSeq,
    at: Date.now() / 1000,
    kind: String(kind || 'unknown'),
    ...payload
  };
  browserEvents.push(event);
  while (browserEvents.length > MAX_BROWSER_EVENTS) browserEvents.shift();
  flushEventWaiters();
  return event;
}

function json(res, status, body) {
  if (res.writableEnded) return;
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store'
  });
  res.end(JSON.stringify(body));
}

function connectedClient() {
  const openClients = [...clients].filter(ws => ws.readyState === WebSocket.OPEN);
  return openClients.length === 1 ? openClients[0] : null;
}

function sendToCurrentProfile(message) {
  const ws = connectedClient();
  if (!ws) return false;
  ws.send(message);
  return true;
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
  const openCount = [...clients].filter(ws => ws.readyState === WebSocket.OPEN).length;
  if (openCount === 1) return true;
  if (openCount === 0) {
    json(res, 503, {
      ok: false,
      error: 'Browser extension is not connected.',
      policy: 'current_connected_browser_profile_only'
    });
    return false;
  }
  json(res, 409, {
    ok: false,
    error: (
      'Multiple browser profiles are connected to this bridge. '
      + 'WAS refuses to guess which profile is current.'
    ),
    extensionClients: openCount,
    policy: 'current_connected_browser_profile_only',
    recovery: (
      'Keep the WAS extension connected in only the browser profile you want to control, '
      + 'or run a separate bridge port for each profile.'
    )
  });
  return false;
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url || '/', `http://${HOST}:${PORT}`);

  if (req.method === 'GET' && url.pathname === '/health') {
    return json(res, 200, {
      ok: true,
      extensionClients: [...clients].filter(ws => ws.readyState === WebSocket.OPEN).length,
      protocolVersion: 4,
      browserEventSeq,
      profilePolicy: 'current_connected_browser_profile_only'
    });
  }

  if (req.method === 'GET' && url.pathname === '/events') {
    const since = Math.max(
      0,
      Number.parseInt(url.searchParams.get('since') || '0', 10) || 0
    );
    const limit = Math.max(
      1,
      Math.min(Number.parseInt(url.searchParams.get('limit') || '100', 10) || 100, 512)
    );
    const waitMs = Math.max(
      0,
      Math.min(Number.parseInt(url.searchParams.get('waitMs') || '0', 10) || 0, 30000)
    );
    const events = browserEventsSince(since, limit);
    if (events.length || waitMs <= 0) {
      return json(res, 200, { ok: true, events, latestSeq: browserEventSeq });
    }
    const waiter = {
      res,
      since,
      limit,
      timer: setTimeout(() => {
        eventWaiters.delete(waiter);
        json(res, 200, { ok: true, events: [], latestSeq: browserEventSeq });
      }, waitMs)
    };
    eventWaiters.add(waiter);
    res.on('close', () => {
      if (res.writableEnded || !eventWaiters.has(waiter)) return;
      clearTimeout(waiter.timer);
      eventWaiters.delete(waiter);
    });
    return;
  }

  if (req.method === 'GET' && url.pathname === '/tabs') {
    if (!ensureClient(res)) return;
    const id = crypto.randomUUID();
    awaitResult(id, 'tabs-result', res);
    sendToCurrentProfile(JSON.stringify({ type: 'list-tabs', id }));
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
    sendToCurrentProfile(JSON.stringify({ type: 'inspect', id, tabId, tail }));
    return;
  }

  if (req.method === 'POST' && (url.pathname === '/open' || url.pathname === '/close' || url.pathname === '/send')) {
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
        sendToCurrentProfile(JSON.stringify({
          type: 'open-tab',
          id,
          url: target,
          active: Boolean(data.active)
        }));
        return;
      }

      if (url.pathname === '/close') {
        const tabId = Number.isInteger(data.tabId) ? data.tabId : undefined;
        if (!Number.isInteger(tabId)) {
          return json(res, 400, { ok: false, error: 'tabId is required.' });
        }
        awaitResult(id, 'close-result', res, 8000);
        sendToCurrentProfile(JSON.stringify({ type: 'close-tab', id, tabId }));
        return;
      }

      const text = String(data.text || '').trim();
      if (!text) return json(res, 400, { ok: false, error: 'text is required.' });
      const tabId = Number.isInteger(data.tabId) ? data.tabId : undefined;
      const commandId = String(data.commandId || '').trim().slice(0, 128);
      awaitResult(id, 'send-result', res, 8000);
      sendToCurrentProfile(JSON.stringify({ type: 'send', id, tabId, text, commandId }));
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
  appendBrowserEvent('extension_connected', {
    extensionClients: [...clients].filter(client => client.readyState === WebSocket.OPEN).length
  });

  ws.on('message', raw => {
    try {
      const msg = JSON.parse(raw.toString());
      if (msg.type === 'ping') return;
      if (msg.type === 'browser-event') {
        const event = msg.event && typeof msg.event === 'object' ? msg.event : {};
        appendBrowserEvent(event.kind || 'browser_event', {
          sourceAt: Number(event.at || 0) || null,
          tab: event.tab && typeof event.tab === 'object' ? event.tab : null,
          tabId: Number.isInteger(event.tabId) ? event.tabId : null,
          windowId: Number.isInteger(event.windowId) ? event.windowId : null,
          changes: event.changes && typeof event.changes === 'object' ? event.changes : {}
        });
        return;
      }
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

  ws.on('close', () => {
    clients.delete(ws);
    appendBrowserEvent('extension_disconnected', {
      extensionClients: [...clients].filter(client => client.readyState === WebSocket.OPEN).length
    });
  });
});

server.listen(PORT, HOST, () => {
  console.error(`Web Agent Swarm bridge listening on http://${HOST}:${PORT}`);
});

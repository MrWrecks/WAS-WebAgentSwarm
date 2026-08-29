const DEFAULT_WS = 'ws://127.0.0.1:8788/ws';

let socket = null;
let reconnectTimer = null;
let keepAliveTimer = null;

async function getWsUrl() {
  const result = await chrome.storage.local.get(['bridgeWsUrl']);
  return String(result.bridgeWsUrl || DEFAULT_WS);
}

function sendSocket(payload) {
  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(payload));
  }
}

async function resolveChatTab(tabId) {
  if (Number.isInteger(tabId)) {
    try {
      const tab = await chrome.tabs.get(tabId);
      if (tab?.url?.startsWith('https://chatgpt.com/')) return tab;
    } catch {}
    return null;
  }
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  return tabs.find(tab => tab.url?.startsWith('https://chatgpt.com/')) || null;
}

async function callContent(tab, message) {
  try {
    return await chrome.tabs.sendMessage(tab.id, message);
  } catch {
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ['content.js']
    });
    return await chrome.tabs.sendMessage(tab.id, message);
  }
}

async function inspect(payload) {
  const tab = await resolveChatTab(payload.tabId);
  if (!tab?.id) {
    sendSocket({
      type: 'inspect-result',
      id: payload.id,
      ok: false,
      error: 'Target tab was not found or is not ChatGPT.'
    });
    return;
  }
  try {
    const result = await callContent(tab, {
      type: 'inspect-chatgpt',
      tail: payload.tail
    });
    sendSocket({
      type: 'inspect-result',
      id: payload.id,
      ok: Boolean(result?.ok),
      error: result?.error || null,
      tab: {
        id: tab.id,
        windowId: tab.windowId,
        index: tab.index,
        title: tab.title || '',
        url: tab.url || '',
        active: Boolean(tab.active)
      },
      snapshot: result?.snapshot || null
    });
  } catch (error) {
    sendSocket({
      type: 'inspect-result',
      id: payload.id,
      ok: false,
      error: String(error)
    });
  }
}

async function send(payload) {
  const tab = await resolveChatTab(payload.tabId);
  if (!tab?.id) {
    sendSocket({
      type: 'send-result',
      id: payload.id,
      ok: false,
      error: 'Target tab was not found or is not ChatGPT.'
    });
    return;
  }
  try {
    const result = await callContent(tab, {
      type: 'send-chatgpt-message',
      text: payload.text
    });
    sendSocket({
      type: 'send-result',
      id: payload.id,
      ok: Boolean(result?.ok),
      error: result?.error || null,
      tabId: tab.id,
      tabTitle: tab.title || '',
      tabUrl: tab.url || ''
    });
  } catch (error) {
    sendSocket({
      type: 'send-result',
      id: payload.id,
      ok: false,
      error: String(error)
    });
  }
}

async function connect() {
  if (socket && (
    socket.readyState === WebSocket.OPEN ||
    socket.readyState === WebSocket.CONNECTING
  )) return;

  const wsUrl = await getWsUrl();
  try {
    socket = new WebSocket(wsUrl);
  } catch {
    scheduleReconnect();
    return;
  }

  socket.onopen = () => {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
    clearInterval(keepAliveTimer);
    keepAliveTimer = setInterval(() => sendSocket({ type: 'ping' }), 20000);
  };

  socket.onmessage = async event => {
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch {
      return;
    }

    if (payload.type === 'list-tabs') {
      const tabs = await chrome.tabs.query({});
      sendSocket({
        type: 'tabs-result',
        id: payload.id,
        tabs: tabs.map(tab => ({
          id: tab.id,
          windowId: tab.windowId,
          index: tab.index,
          title: tab.title || '',
          url: tab.url || '',
          active: Boolean(tab.active),
          pinned: Boolean(tab.pinned)
        }))
      });
      return;
    }

    if (payload.type === 'open-tab') {
      try {
        const tab = await chrome.tabs.create({
          url: payload.url,
          active: Boolean(payload.active)
        });
        sendSocket({
          type: 'open-result',
          id: payload.id,
          ok: true,
          tab: {
            id: tab.id,
            windowId: tab.windowId,
            index: tab.index,
            title: tab.title || '',
            url: tab.url || payload.url || '',
            active: Boolean(tab.active),
            pinned: Boolean(tab.pinned)
          }
        });
      } catch (error) {
        sendSocket({
          type: 'open-result',
          id: payload.id,
          ok: false,
          error: String(error)
        });
      }
      return;
    }

    if (payload.type === 'inspect') {
      await inspect(payload);
      return;
    }

    if (payload.type === 'send' && typeof payload.text === 'string') {
      await send(payload);
    }
  };

  socket.onclose = scheduleReconnect;
  socket.onerror = () => {
    try { socket.close(); } catch {}
  };
}

function scheduleReconnect() {
  clearInterval(keepAliveTimer);
  keepAliveTimer = null;
  socket = null;
  if (!reconnectTimer) {
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connect();
    }, 1500);
  }
}

chrome.storage.onChanged.addListener(changes => {
  if (changes.bridgeWsUrl) {
    try { socket?.close(); } catch {}
  }
});

chrome.runtime.onInstalled.addListener(connect);
chrome.runtime.onStartup.addListener(connect);
connect();

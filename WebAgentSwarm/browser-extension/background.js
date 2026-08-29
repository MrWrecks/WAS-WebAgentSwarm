const DEFAULT_WS = 'ws://127.0.0.1:8788/ws';

let socket = null;
let reconnectTimer = null;
let keepAliveTimer = null;
let connectInFlight = false;

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

async function waitForTabComplete(tabId, timeoutMs = 15000) {
  return await new Promise(resolve => {
    let finished = false;
    let timer = null;

    const finish = async () => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      chrome.tabs.onUpdated.removeListener(onUpdated);
      try {
        resolve(await chrome.tabs.get(tabId));
      } catch {
        resolve(null);
      }
    };

    const onUpdated = (updatedTabId, changeInfo) => {
      if (updatedTabId === tabId && changeInfo.status === 'complete') {
        finish();
      }
    };

    chrome.tabs.onUpdated.addListener(onUpdated);
    timer = setTimeout(finish, timeoutMs);

    chrome.tabs.get(tabId)
      .then(tab => {
        if (tab?.status === 'complete') finish();
      })
      .catch(finish);
  });
}

async function callContent(tab, message) {
  const readyTab = (
    tab?.status === 'complete'
      ? tab
      : await waitForTabComplete(tab.id)
  );
  if (!readyTab?.id || !readyTab.url?.startsWith('https://chatgpt.com/')) {
    throw new Error('Target ChatGPT tab did not finish loading.');
  }

  try {
    return await chrome.tabs.sendMessage(readyTab.id, message);
  } catch {
    await chrome.scripting.executeScript({
      target: { tabId: readyTab.id },
      files: ['content.js']
    });
    return await chrome.tabs.sendMessage(readyTab.id, message);
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
  if (connectInFlight) return;
  if (socket && (
    socket.readyState === WebSocket.OPEN ||
    socket.readyState === WebSocket.CONNECTING
  )) return;

  connectInFlight = true;
  let currentSocket = null;

  try {
    const wsUrl = await getWsUrl();

    if (socket && (
      socket.readyState === WebSocket.OPEN ||
      socket.readyState === WebSocket.CONNECTING
    )) return;

    currentSocket = new WebSocket(wsUrl);
    socket = currentSocket;
  } catch {
    scheduleReconnect();
    return;
  } finally {
    connectInFlight = false;
  }

  currentSocket.onopen = () => {
    if (socket !== currentSocket) return;
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
    clearInterval(keepAliveTimer);
    keepAliveTimer = setInterval(() => sendSocket({ type: 'ping' }), 20000);
  };

  currentSocket.onmessage = async event => {
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
        const created = await chrome.tabs.create({
          url: payload.url,
          active: Boolean(payload.active)
        });
        const tab = await waitForTabComplete(created.id);
        if (!tab?.id || !tab.url?.startsWith('https://chatgpt.com/')) {
          throw new Error('New ChatGPT tab did not finish loading.');
        }
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

  currentSocket.onclose = () => {
    if (socket === currentSocket) scheduleReconnect();
  };
  currentSocket.onerror = () => {
    if (socket !== currentSocket) return;
    try { currentSocket.close(); } catch {}
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

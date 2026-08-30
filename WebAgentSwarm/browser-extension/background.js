const DEFAULT_WS = 'ws://127.0.0.1:8788/ws';

let socket = null;
let reconnectTimer = null;
let keepAliveTimer = null;
let connectInFlight = false;
const commandResults = new Map();
const commandInflight = new Map();
const knownChatTabs = new Map();
const MAX_COMMAND_CACHE = 256;

function isChatGptTab(tab) {
  return Boolean(tab?.id && tab?.url?.startsWith('https://chatgpt.com/'));
}

function serializeTab(tab) {
  return {
    id: tab.id,
    windowId: tab.windowId,
    index: tab.index,
    title: tab.title || '',
    url: tab.url || '',
    active: Boolean(tab.active),
    pinned: Boolean(tab.pinned),
    status: tab.status || '',
    discarded: Boolean(tab.discarded),
    frozen: Boolean(tab.frozen),
    autoDiscardable: tab.autoDiscardable !== false
  };
}

async function protectChatTab(tabOrId) {
  let tab = tabOrId;
  if (Number.isInteger(tabOrId)) {
    try {
      tab = await chrome.tabs.get(tabOrId);
    } catch {
      return null;
    }
  }
  if (!isChatGptTab(tab)) {
    if (tab?.id) knownChatTabs.delete(tab.id);
    return tab || null;
  }
  if (tab.autoDiscardable !== false) {
    try {
      tab = await chrome.tabs.update(tab.id, { autoDiscardable: false });
    } catch {}
  }
  if (tab?.id) knownChatTabs.set(tab.id, serializeTab(tab));
  return tab;
}

async function protectAllChatTabs() {
  let tabs = [];
  try {
    tabs = await chrome.tabs.query({ url: 'https://chatgpt.com/*' });
  } catch {
    return;
  }
  await Promise.all(tabs.map(tab => protectChatTab(tab)));
}

async function reviveDiscardedChatTab(tab) {
  let current = await protectChatTab(tab);
  if (!current?.discarded) return current;
  try {
    await chrome.tabs.reload(current.id);
    current = await waitForTabComplete(current.id);
    return await protectChatTab(current);
  } catch {
    return current;
  }
}

function rememberCommand(commandId, result) {
  if (!commandId) return;
  commandResults.set(commandId, result);
  while (commandResults.size > MAX_COMMAND_CACHE) {
    const oldest = commandResults.keys().next().value;
    commandResults.delete(oldest);
  }
}

async function getWsUrl() {
  const result = await chrome.storage.local.get(['bridgeWsUrl']);
  return String(result.bridgeWsUrl || DEFAULT_WS);
}

function sendSocket(payload) {
  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(payload));
  }
}

function emitBrowserEvent(kind, tab = null, changes = {}) {
  sendSocket({
    type: 'browser-event',
    event: {
      kind: String(kind || 'browser_event'),
      at: Date.now() / 1000,
      tab: tab?.id ? serializeTab(tab) : null,
      tabId: Number.isInteger(tab?.id) ? tab.id : null,
      windowId: Number.isInteger(tab?.windowId) ? tab.windowId : null,
      changes: changes && typeof changes === 'object' ? changes : {}
    }
  });
}

async function resolveChatTab(tabId) {
  if (Number.isInteger(tabId)) {
    try {
      const tab = await chrome.tabs.get(tabId);
      if (tab?.url?.startsWith('https://chatgpt.com/')) return await protectChatTab(tab);
    } catch {}
    return null;
  }
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  const tab = tabs.find(item => item.url?.startsWith('https://chatgpt.com/')) || null;
  return await protectChatTab(tab);
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
  let readyTab = await protectChatTab(tab);
  readyTab = await reviveDiscardedChatTab(readyTab);
  readyTab = (
    readyTab?.status === 'complete'
      ? readyTab
      : await waitForTabComplete(readyTab.id)
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
  let tab = await resolveChatTab(payload.tabId);
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
    tab = await reviveDiscardedChatTab(tab);
    const result = await callContent(tab, {
      type: 'inspect-chatgpt',
      tail: payload.tail
    });
    sendSocket({
      type: 'inspect-result',
      id: payload.id,
      ok: Boolean(result?.ok),
      error: result?.error || null,
      tab: serializeTab(tab),
      snapshot: result?.snapshot || null
    });
  } catch (error) {
    let latest = tab;
    try {
      latest = await chrome.tabs.get(tab.id);
    } catch {}
    sendSocket({
      type: 'inspect-result',
      id: payload.id,
      ok: false,
      error: String(error),
      tab: latest?.id ? serializeTab(latest) : null,
      browserState: latest?.discarded
        ? 'discarded'
        : (latest?.frozen ? 'frozen' : 'unresponsive')
    });
  }
}

async function performSend(tab, payload) {
  try {
    const result = await callContent(tab, {
      type: 'send-chatgpt-message',
      text: payload.text
    });
    return {
      ok: Boolean(result?.ok),
      error: result?.error || null,
      tabId: tab.id,
      tabTitle: tab.title || '',
      tabUrl: tab.url || '',
      commandId: String(payload.commandId || '')
    };
  } catch (error) {
    return {
      ok: false,
      error: String(error),
      tabId: tab?.id,
      commandId: String(payload.commandId || '')
    };
  }
}

async function send(payload) {
  const commandId = String(payload.commandId || '').trim();
  if (commandId && commandResults.has(commandId)) {
    sendSocket({
      type: 'send-result',
      id: payload.id,
      ...commandResults.get(commandId),
      duplicate: true
    });
    return;
  }

  if (commandId && commandInflight.has(commandId)) {
    const result = await commandInflight.get(commandId);
    sendSocket({ type: 'send-result', id: payload.id, ...result, duplicate: true });
    return;
  }

  const tab = await resolveChatTab(payload.tabId);
  if (!tab?.id) {
    sendSocket({
      type: 'send-result',
      id: payload.id,
      ok: false,
      error: 'Target tab was not found or is not ChatGPT.',
      commandId
    });
    return;
  }

  const work = performSend(tab, payload);
  if (commandId) commandInflight.set(commandId, work);
  const result = await work;
  if (commandId) {
    commandInflight.delete(commandId);
    rememberCommand(commandId, result);
  }
  sendSocket({ type: 'send-result', id: payload.id, ...result, duplicate: false });
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
    protectAllChatTabs();
    keepAliveTimer = setInterval(() => {
      sendSocket({ type: 'ping' });
      protectAllChatTabs();
    }, 20000);
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
      await Promise.all(tabs.map(tab => protectChatTab(tab)));
      const refreshed = await chrome.tabs.query({});
      sendSocket({
        type: 'tabs-result',
        id: payload.id,
        tabs: refreshed.map(serializeTab)
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
        const protectedTab = await protectChatTab(tab);
        sendSocket({
          type: 'open-result',
          id: payload.id,
          ok: true,
          tab: serializeTab(protectedTab || tab)
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

    if (payload.type === 'close-tab') {
      try {
        const tab = await resolveChatTab(payload.tabId);
        if (!tab?.id) throw new Error('Target tab was not found or is not ChatGPT.');
        await chrome.tabs.remove(tab.id);
        sendSocket({
          type: 'close-result',
          id: payload.id,
          ok: true,
          tabId: tab.id
        });
      } catch (error) {
        sendSocket({
          type: 'close-result',
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

chrome.tabs.onCreated.addListener(tab => {
  protectChatTab(tab).then(protectedTab => {
    if (isChatGptTab(protectedTab)) {
      emitBrowserEvent('tab_created', protectedTab, {});
    }
  });
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  const previous = knownChatTabs.get(tabId) || null;
  const relevant = (
    Object.prototype.hasOwnProperty.call(changeInfo, 'url')
    || Object.prototype.hasOwnProperty.call(changeInfo, 'status')
    || Object.prototype.hasOwnProperty.call(changeInfo, 'discarded')
    || Object.prototype.hasOwnProperty.call(changeInfo, 'frozen')
    || Object.prototype.hasOwnProperty.call(changeInfo, 'autoDiscardable')
  );
  protectChatTab(tab || tabId).then(protectedTab => {
    if (isChatGptTab(protectedTab)) {
      if (relevant) emitBrowserEvent('tab_lifecycle', protectedTab, changeInfo);
      return;
    }
    if (previous && relevant) {
      emitBrowserEvent('tab_unmanaged', previous, changeInfo);
      knownChatTabs.delete(tabId);
    }
  });
});

chrome.tabs.onRemoved.addListener((tabId, removeInfo) => {
  const previous = knownChatTabs.get(tabId) || null;
  if (!previous) return;
  knownChatTabs.delete(tabId);
  emitBrowserEvent('tab_removed', previous, {
    isWindowClosing: Boolean(removeInfo?.isWindowClosing)
  });
});

chrome.tabs.onActivated.addListener(activeInfo => {
  chrome.tabs.get(activeInfo.tabId)
    .then(tab => {
      if (isChatGptTab(tab)) emitBrowserEvent('tab_activated', tab, {});
    })
    .catch(() => {});
});

chrome.runtime.onInstalled.addListener(() => {
  protectAllChatTabs();
  connect();
});
chrome.runtime.onStartup.addListener(() => {
  protectAllChatTabs();
  connect();
});
protectAllChatTabs();
connect();

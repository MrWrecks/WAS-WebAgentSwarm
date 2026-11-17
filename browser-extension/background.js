const DEFAULT_WS = 'ws://127.0.0.1:8788/ws';
const RECONNECT_ALARM = 'wasBridgeReconnect';
const PROTOCOL_MIN = 5;
const PROTOCOL_MAX = 5;
const COMMAND_CACHE_KEY = 'wasCommandResultsV1';

let socket = null;
let reconnectTimer = null;
let keepAliveTimer = null;
let connectInFlight = false;
let bridgeHandshakeOk = false;
let commandCacheReady = false;
let persistQueue = Promise.resolve();
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

async function protectChatTab(tabOrId, shouldProtect = false) {
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
  if (shouldProtect && tab.autoDiscardable !== false) {
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

async function persistCommandResults() {
  const entries = [];
  for (const [commandId, result] of commandResults.entries()) {
    entries.push({ commandId, result, recordedAt: Date.now() / 1000 });
  }
  persistQueue = persistQueue.catch(() => {}).then(
    () => chrome.storage.local.set({ [COMMAND_CACHE_KEY]: entries.slice(-MAX_COMMAND_CACHE) })
  );
  await persistQueue;
}

async function hydrateCommandResults() {
  try {
    const stored = await chrome.storage.local.get([COMMAND_CACHE_KEY]);
    const entries = Array.isArray(stored[COMMAND_CACHE_KEY]) ? stored[COMMAND_CACHE_KEY] : [];
    for (const entry of entries.slice(-MAX_COMMAND_CACHE)) {
      const commandId = String(entry?.commandId || '').trim();
      if (!commandId || !entry?.result || typeof entry.result !== 'object') continue;
      commandResults.set(commandId, entry.result);
    }
    commandCacheReady = true;
  } catch {
    // Fail closed. An unreadable command cache might contain completed sends.
    commandCacheReady = false;
  }
}

async function rememberCommand(commandId, result, requireDurability = false) {
  if (!commandId) return;
  commandResults.set(commandId, { ...result, commandId });
  while (commandResults.size > MAX_COMMAND_CACHE) {
    const oldest = commandResults.keys().next().value;
    commandResults.delete(oldest);
  }
  try {
    await persistCommandResults();
  } catch (error) {
    if (requireDurability) {
      // Keep the uncertain reservation in memory even if storage fails.
      // Never let an immediate retry reuse this command ID.
      throw error;
    }
  }
}

async function getWsUrl() {
  const result = await chrome.storage.local.get(['bridgeWsUrl', 'bridgeToken']);
  const raw = String(result.bridgeWsUrl || DEFAULT_WS).trim();
  const token = String(result.bridgeToken || '').trim();
  if (token.length < 32) {
    throw new Error('Bridge token is not configured. Open WAS extension options.');
  }
  const url = new URL(raw);
  url.searchParams.set('token', token);
  return url.toString();
}

function sendSocket(payload) {
  if (
    socket?.readyState === WebSocket.OPEN
    && (bridgeHandshakeOk || payload?.type === 'hello')
  ) {
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
      if (tab?.url?.startsWith('https://chatgpt.com/')) return await protectChatTab(tab, true);
    } catch {}
    return null;
  }
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  const tab = tabs.find(item => item.url?.startsWith('https://chatgpt.com/')) || null;
  return await protectChatTab(tab, true);
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
      text: payload.text,
      expectedUrl: payload.expectedUrl || tab.url
    });
    const uncertain = !result?.ok && result?.mayHaveExecuted !== false;
    return {
      ok: Boolean(result?.ok),
      error: result?.error || null,
      mayHaveExecuted: uncertain,
      uncertain,
      terminal: Boolean(result?.terminal),
      tabId: tab.id,
      tabTitle: tab.title || '',
      tabUrl: tab.url || '',
      commandId: String(payload.commandId || '')
    };
  } catch (error) {
    return {
      ok: false,
      error: String(error),
      mayHaveExecuted: true,
      uncertain: true,
      tabId: tab?.id,
      commandId: String(payload.commandId || '')
    };
  }
}

async function sendFingerprint(tabId, text, expectedUrl) {
  const bytes = new TextEncoder().encode(JSON.stringify([tabId, text, expectedUrl]));
  const hash = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(hash), b => b.toString(16).padStart(2, '0')).join('');
}

async function executeSend(payload, fingerprint) {
  const commandId = String(payload.commandId).trim();
  const cached = commandResults.get(commandId);
  if (cached) return { result: cached, duplicate: true };

  const tab = await resolveChatTab(payload.tabId);
  if (!tab?.id || (payload.expectedUrl && tab.url !== payload.expectedUrl)) {
    const result = {
      ok: false,
      error: !tab?.id ? 'Target tab was not found or is not ChatGPT.'
        : 'Target conversation changed before submission; nothing was submitted.',
      commandId, fingerprint, terminal: true, mayHaveExecuted: false
    };
    await rememberCommand(commandId, result);
    return { result, duplicate: false };
  }

  // Reserve the command ID durably before any browser submission.
  // Unknown completion remains unknown after worker or browser restarts.
  try {
    await rememberCommand(commandId, {
      ok: false,
      uncertain: true,
      mayHaveExecuted: true,
      tabId: tab.id,
      fingerprint,
      error: 'Send outcome unknown; inspect the target conversation before retrying.'
    }, true);
  } catch {
    return {
      result: {
        ok: false, commandId, fingerprint, mayHaveExecuted: false,
        error: 'Could not persist send intent; nothing was submitted.'
      },
      duplicate: false
    };
  }

  const result = { ...await performSend(tab, payload), fingerprint };
  await rememberCommand(commandId, result);
  return { result, duplicate: false };
}

async function send(payload) {
  const commandId = String(payload.commandId || '').trim();
  if (!commandId || !Number.isInteger(payload.tabId) || payload.tabId <= 0
      || typeof payload.text !== 'string' || !payload.text.trim()) {
    sendSocket({
      type: 'send-result', id: payload.id, ok: false, commandId,
      error: 'A command ID, explicit tab ID, and nonempty text are required.'
    });
    return;
  }
  if (!commandCacheReady) {
    sendSocket({
      type: 'send-result', id: payload.id, ok: false, commandId,
      error: 'Durable command cache could not be loaded; send was not attempted.'
    });
    return;
  }

  const fingerprintPromise = sendFingerprint(
    payload.tabId, payload.text, String(payload.expectedUrl || '')
  );
  const inFlight = commandInflight.get(commandId);
  // Register the work before its first await, so concurrent duplicates
  // cannot pass through the durability check and submit twice.
  const work = inFlight || (async () => {
    const fingerprint = await fingerprintPromise;
    return executeSend(payload, fingerprint);
  })();
  if (!inFlight) commandInflight.set(commandId, work);
  try {
    const [outcome, requested] = await Promise.all([work, fingerprintPromise]);
    if (outcome.result.fingerprint !== requested) {
      sendSocket({
        type: 'send-result', id: payload.id, ok: false, commandId,
        duplicate: true, uncertain: true, mayHaveExecuted: true,
        terminal: true,
        error: 'Command ID is already associated with another tab or message; nothing was submitted.'
      });
      return;
    }
    sendSocket({
      type: 'send-result', id: payload.id,
      ...outcome.result, duplicate: Boolean(inFlight || outcome.duplicate)
    });
  } catch (error) {
    sendSocket({
      type: 'send-result', id: payload.id, ok: false, commandId,
      uncertain: true, mayHaveExecuted: true,
      error: 'Command delivery status is uncertain: ' + String(error)
    });
  } finally {
    if (!inFlight && commandInflight.get(commandId) === work) {
      commandInflight.delete(commandId);
    }
  }
}

async function commandStatus(payload) {
  const commandId = String(payload.commandId || '').trim();
  if (commandResults.has(commandId)) {
    const result = commandResults.get(commandId);
    sendSocket({
      type: 'command-status-result',
      id: payload.id,
      ok: true,
      found: true,
      completed: !result.uncertain,
      commandId,
      result
    });
    return;
  }
  if (commandInflight.has(commandId)) {
    sendSocket({
      type: 'command-status-result',
      id: payload.id,
      ok: true,
      found: true,
      completed: false,
      commandId
    });
    return;
  }
  sendSocket({
    type: 'command-status-result',
    id: payload.id,
    ok: true,
    found: false,
    completed: false,
    commandId
  });
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
    bridgeHandshakeOk = false;
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
    clearInterval(keepAliveTimer);
    currentSocket.send(JSON.stringify({
      type: 'hello',
      extensionVersion: chrome.runtime.getManifest().version,
      protocolMin: PROTOCOL_MIN,
      protocolMax: PROTOCOL_MAX
    }));
  };

  currentSocket.onmessage = async event => {
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch {
      return;
    }

    if (payload.type === 'hello-ack') {
      const bridgeMin = Number(payload.protocolMin || 0);
      const bridgeMax = Number(payload.protocolMax || 0);
      const compatible = Boolean(payload.ok)
        && bridgeMax >= PROTOCOL_MIN
        && bridgeMin <= PROTOCOL_MAX;
      if (!compatible) {
        bridgeHandshakeOk = false;
        try { currentSocket.close(1002, 'Incompatible WAS protocol'); } catch {}
        return;
      }
      bridgeHandshakeOk = true;
      protectAllChatTabs();
      clearInterval(keepAliveTimer);
      keepAliveTimer = setInterval(() => {
        sendSocket({ type: 'ping' });
        protectAllChatTabs();
      }, 20000);
      return;
    }

    if (!bridgeHandshakeOk) return;

    if (payload.type === 'list-tabs') {
      const tabs = await chrome.tabs.query({ url: 'https://chatgpt.com/*' });
      await Promise.all(tabs.map(tab => protectChatTab(tab)));
      const refreshed = await chrome.tabs.query({ url: 'https://chatgpt.com/*' });
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
        const protectedTab = await protectChatTab(tab, true);
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
        if (payload.expectedUrl && tab.url !== payload.expectedUrl) {
          throw new Error('Target tab navigated to another conversation; refusing to close it.');
        }
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

    if (payload.type === 'command-status') {
      await commandStatus(payload);
      return;
    }

    if (payload.type === 'send' && typeof payload.text === 'string') {
      await send(payload);
    }
  };

  currentSocket.onclose = () => {
    bridgeHandshakeOk = false;
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
  bridgeHandshakeOk = false;
  if (!reconnectTimer) {
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connect();
    }, 1500);
  }
}

function ensureReconnectAlarm() {
  try {
    chrome.alarms.create(RECONNECT_ALARM, { periodInMinutes: 0.5 });
  } catch {}
}

chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm?.name !== RECONNECT_ALARM) return;
  if (!socket || socket.readyState !== WebSocket.OPEN) connect();
});

chrome.storage.onChanged.addListener(changes => {
  if (changes.bridgeWsUrl || changes.bridgeToken) {
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
  ensureReconnectAlarm();
  protectAllChatTabs();
  connect();
});
chrome.runtime.onStartup.addListener(() => {
  ensureReconnectAlarm();
  protectAllChatTabs();
  connect();
});
ensureReconnectAlarm();
protectAllChatTabs();
hydrateCommandResults().finally(() => connect());

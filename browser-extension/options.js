const input = document.querySelector('#ws');
const tokenInput = document.querySelector('#token');
const status = document.querySelector('#status');

chrome.storage.local.get(['bridgeWsUrl', 'bridgeToken']).then(result => {
  if (result.bridgeWsUrl) input.value = result.bridgeWsUrl;
  if (result.bridgeToken) tokenInput.value = result.bridgeToken;
});

document.querySelector('#save').addEventListener('click', async () => {
  const value = input.value.trim();
  const token = tokenInput.value.trim();
  if (!value.startsWith('ws://127.0.0.1:') && !value.startsWith('ws://localhost:')) {
    status.textContent = 'Use a loopback WebSocket URL.';
    return;
  }
  if (token.length < 32) {
    status.textContent = 'Bridge token is required.';
    return;
  }
  await chrome.storage.local.set({ bridgeWsUrl: value, bridgeToken: token });
  status.textContent = 'Saved. The extension will reconnect.';
  setTimeout(() => { status.textContent = ''; }, 1800);
});

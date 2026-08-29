const input = document.querySelector('#ws');
const status = document.querySelector('#status');

chrome.storage.local.get(['bridgeWsUrl']).then(result => {
  if (result.bridgeWsUrl) input.value = result.bridgeWsUrl;
});

document.querySelector('#save').addEventListener('click', async () => {
  const value = input.value.trim();
  if (!value.startsWith('ws://127.0.0.1:') && !value.startsWith('ws://localhost:')) {
    status.textContent = 'Use a loopback WebSocket URL.';
    return;
  }
  await chrome.storage.local.set({ bridgeWsUrl: value });
  status.textContent = 'Saved.';
  setTimeout(() => { status.textContent = ''; }, 1200);
});

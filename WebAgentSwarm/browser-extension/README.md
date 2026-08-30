# Browser extension

This Manifest V3 extension is the browser-side companion.

## Install in Edge

1. Open `edge://extensions/`.
2. Enable **Developer mode**.
3. Click **Load unpacked**.
4. Select this `browser-extension` directory.
5. Open the extension details and then its **Extension options**.
6. Keep the default bridge URL `ws://127.0.0.1:8788/ws` unless you changed the bridge port.
7. Reload any already-open ChatGPT tabs once after first install if needed.

## Install in Chrome

Use `chrome://extensions/` and the same unpacked-extension flow.

The extension reads only bounded visible state from ChatGPT pages and can insert/send
messages when instructed by the local bridge.

## Agent-tab lifecycle protection

Every ChatGPT tab observed or created by WAS is automatically marked
`autoDiscardable=false`, so Chromium/Edge should not automatically discard it
under memory pressure. Tab metadata also reports `discarded`, `frozen`, and
`autoDiscardable`. A discarded tab is reloaded before WAS inspects it or sends
a message.

Browser/OS energy policies may still freeze a minimized/background page. WAS
treats that as `browser_suspended` transport liveness, not worker completion or
safe scale-in evidence.

## Browser-profile behavior

The extension is profile-scoped. WAS controls the Edge/Chrome profile where this unpacked
extension is actually running and connected to the bridge. New ChatGPT Agent tabs are created
with `chrome.tabs.create()` inside that same profile, so the existing login state is reused.

WAS does not launch another browser profile. If you have the extension installed in multiple
profiles, do not connect them to the same bridge port at the same time; the bridge intentionally
fails closed on profile ambiguity.

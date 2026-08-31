(() => {
  if (globalThis.__WEB_AGENT_SWARM_CONTENT__) return;
  globalThis.__WEB_AGENT_SWARM_CONTENT__ = true;

  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

  function composer() {
    return (
      document.querySelector('#prompt-textarea') ||
      document.querySelector('div[contenteditable="true"][role="textbox"]') ||
      document.querySelector('textarea')
    );
  }

  function sendButton() {
    return (
      document.querySelector('[data-testid="send-button"]') ||
      document.querySelector('button[aria-label*="Send"]') ||
      document.querySelector('button[aria-label*="发送"]')
    );
  }

  function generating() {
    return Boolean(
      document.querySelector('[data-testid="stop-button"]') ||
      document.querySelector('button[aria-label*="Stop generating"]') ||
      document.querySelector('button[aria-label*="停止生成"]')
    );
  }

  function boundedText(value, limit = 12000) {
    const text = String(value || '').replace(/\u00a0/g, ' ').trim();
    return text.length <= limit ? text : text.slice(0, limit) + '\n...[truncated]';
  }

  function snapshot(tail = 6) {
    const n = Math.max(1, Math.min(Number(tail) || 6, 20));
    const messages = Array.from(
      document.querySelectorAll('[data-message-author-role]')
    )
      .filter(node => ['user', 'assistant'].includes(
        node.getAttribute('data-message-author-role')
      ))
      .map((node, index) => ({
        index,
        role: node.getAttribute('data-message-author-role') || '',
        messageId:
          node.getAttribute('data-message-id') ||
          node.closest('[data-message-id]')?.getAttribute('data-message-id') ||
          '',
        text: boundedText(node.innerText || node.textContent || '')
      }));

    const last = messages[messages.length - 1] || null;
    const lastUser = [...messages].reverse().find(item => item.role === 'user') || null;
    const lastAssistant = [...messages].reverse().find(item => item.role === 'assistant') || null;
    const busy = generating();
    const visibleText = String(document.body?.innerText || '');
    const connectionInterrupted = (
      visibleText.includes('连接已中断') ||
      visibleText.includes('正在等待完整回复') ||
      visibleText.includes('Connection interrupted') ||
      visibleText.includes('waiting for a complete response')
    );
    const retryActionPresent = Array.from(document.querySelectorAll('button')).some(button => {
      const label = String(
        button.innerText || button.textContent || button.getAttribute('aria-label') || ''
      ).trim().toLowerCase();
      return label === '重试' || label === 'retry' || label.includes('retry');
    });
    const requestFailed = Boolean(
      !busy && retryActionPresent && (
        visibleText.includes('消息发送超时') ||
        visibleText.includes('请重试') ||
        visibleText.includes('Message send timed out') ||
        visibleText.includes('Please retry')
      )
    );
    const conversationLimitReached = (
      visibleText.includes('你已达到此对话的长度上限') ||
      visibleText.includes('达到此对话的长度上限') ||
      visibleText.includes('开始新聊天以继续对话') ||
      visibleText.includes("You've reached the maximum length for this conversation") ||
      visibleText.includes('You have reached the maximum length for this conversation') ||
      visibleText.includes('start a new chat to continue')
    );

    return {
      title: document.title,
      url: location.href,
      uiState: conversationLimitReached
        ? 'conversation_limit'
        : (busy ? 'generating' : 'idle'),
      generating: busy,
      connectionInterrupted,
      requestFailed,
      retryActionPresent,
      conversationLimitReached,
      stuckGenerating: Boolean(busy && connectionInterrupted),
      composerPresent: Boolean(composer()),
      sendButtonEnabled: Boolean(sendButton() && !sendButton().disabled),
      messageCount: messages.length,
      lastRole: last?.role || null,
      lastUserText: lastUser?.text || '',
      lastAssistantText: lastAssistant?.text || '',
      messages: messages.slice(-n)
    };
  }

  async function setText(target, text) {
    target.focus();
    if (target instanceof HTMLTextAreaElement || target instanceof HTMLInputElement) {
      const proto = target instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype;
      const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
      descriptor?.set?.call(target, text);
      target.dispatchEvent(new Event('input', { bubbles: true }));
      target.dispatchEvent(new Event('change', { bubbles: true }));
      return;
    }

    target.replaceChildren();
    const p = document.createElement('p');
    p.textContent = text;
    target.appendChild(p);
    target.dispatchEvent(new InputEvent('input', {
      bubbles: true,
      inputType: 'insertText',
      data: text
    }));
  }

  async function sendMessage(text) {
    if (generating()) {
      return { ok: false, error: 'Target tab is generating.' };
    }

    let target = composer();
    for (let i = 0; i < 20 && !target; i += 1) {
      await sleep(250);
      target = composer();
    }
    if (!target) return { ok: false, error: 'Composer not found.' };

    await setText(target, text);
    await sleep(250);

    let button = sendButton();
    for (let i = 0; i < 12 && (!button || button.disabled); i += 1) {
      await sleep(150);
      button = sendButton();
    }
    if (!button || button.disabled) {
      return { ok: false, error: 'Send button not found or not enabled.' };
    }

    button.click();
    return { ok: true };
  }

  chrome.runtime.onMessage.addListener((message, _sender, respond) => {
    if (message?.type === 'inspect-chatgpt') {
      try {
        respond({ ok: true, snapshot: snapshot(message.tail) });
      } catch (error) {
        respond({ ok: false, error: String(error) });
      }
      return;
    }

    if (message?.type === 'send-chatgpt-message') {
      sendMessage(String(message.text || ''))
        .then(respond)
        .catch(error => respond({ ok: false, error: String(error) }));
      return true;
    }
  });
})();

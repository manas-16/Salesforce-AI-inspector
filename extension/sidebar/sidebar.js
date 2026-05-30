// ─── Salesforce (AI)nspector — sidebar.js ────────────────────────────────────

(function () {
  'use strict';

  let BACKEND_URL = 'http://localhost:8000';

  // ─── STATE ───────────────────────────────────────────────────────────────
  let orgContext = null;
  let apiKey     = null;
  let llmProvider = 'anthropic';
  let attachedFile = null;
  let isLoading = false;
  let conversationHistory = [];

  // ─── DOM REFS ─────────────────────────────────────────────────────────────
  const chatContainer   = document.getElementById('chat-container');
  const userInput       = document.getElementById('user-input');
  const btnSend         = document.getElementById('btn-send');
  const btnClear        = document.getElementById('btn-clear');
  const btnSettings     = document.getElementById('btn-settings');
  const fileInput       = document.getElementById('file-input');
  const filePreview     = document.getElementById('file-preview');
  const filePreviewName = document.getElementById('file-preview-name');
  const btnRemoveFile   = document.getElementById('btn-remove-file');
  const orgBanner       = document.getElementById('org-banner');
  const orgStatusText   = document.getElementById('org-status-text');
  const prodWarning     = document.getElementById('prod-warning');
  const sendIcon        = document.getElementById('send-icon');
  const loadingSpinner  = document.getElementById('loading-spinner');

  // ─── 1. INIT ──────────────────────────────────────────────────────────────

  async function init() {
    const stored = await new Promise(r =>
      chrome.storage.local.get(
        ['sfAccessToken', 'sfInstanceUrl', 'backendUrl',
         'anthropicApiKey', 'openaiApiKey', 'googleApiKey', 'llmProvider'],
        r
      )
    );

    // Backend URL
    if (stored.backendUrl) BACKEND_URL = stored.backendUrl;

    // LLM provider + key
    llmProvider = stored.llmProvider || 'anthropic';
    apiKey      = stored[`${llmProvider}ApiKey`] || null;

    if (!apiKey) {
      showOrgBanner('error', 'No API key. Click ⚙ to configure.');
    }

    // Salesforce session from OAuth token
    if (stored.sfAccessToken && stored.sfInstanceUrl) {
      orgContext = {
        sessionId:            stored.sfAccessToken,
        instanceUrl:          stored.sfInstanceUrl,
        pageContext:          {},
        isProbablyProduction: false,
      };
      showOrgBanner('connected', `Connected · ${new URL(stored.sfInstanceUrl).hostname}`);
    } else {
      showOrgBanner('error', 'Not connected. Click ⚙ → Connect to Salesforce.');
    }

    // Still listen for page context from content.js (URL, breadcrumb, errors)
    window.parent.postMessage({ type: 'REQUEST_CONTEXT' }, '*');
  }

  // ─── 2. PAGE CONTEXT (from content.js) ────────────────────────────────────
  // We use this for page context only — NOT for session

  window.addEventListener('message', (event) => {
    if (event.data?.type !== 'SF_CONTEXT') return;

    const ctx = event.data;

    // Merge page context into orgContext if session already set via OAuth
    if (orgContext) {
      orgContext.pageContext = ctx.pageContext || {};
      // Don't overwrite sessionId — keep the OAuth token
    }

    if (ctx.pageContext?.heading) {
      appendContextHint(ctx.pageContext);
    }
  });

  function showOrgBanner(status, text) {
    orgBanner.classList.remove('hidden', 'connected', 'error', 'connecting');
    orgBanner.classList.add(status);
    orgStatusText.textContent = text;
  }

  function appendContextHint(pageContext) {
    const hint = document.createElement('div');
    hint.style.cssText = `
      font-size: 11px; color: #6b7a90; text-align: center;
      padding: 4px 8px; background: #f4f6f9;
      border-radius: 6px; margin: 0 8px;
    `;
    hint.textContent = `📍 ${pageContext.heading || pageContext.url || ''}`;
    chatContainer.appendChild(hint);
  }

  // ─── 3. FILE HANDLING ─────────────────────────────────────────────────────

  fileInput.addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    const allowedExts = ['.csv', '.json', '.cls', '.xml', '.xlsx', '.txt'];
    const ext = '.' + file.name.split('.').pop().toLowerCase();

    if (!allowedExts.includes(ext)) {
      showError('Unsupported file type. Allowed: CSV, JSON, XLSX, .cls, XML');
      return;
    }
    if (file.size > 5 * 1024 * 1024) {
      showError('File too large. Maximum 5MB.');
      return;
    }

    try {
      const content = await readFile(file);
      attachedFile  = { name: file.name, content, type: ext };
      filePreviewName.textContent = `📎 ${file.name}`;
      filePreview.classList.remove('hidden');
    } catch {
      showError('Failed to read file.');
    }
    fileInput.value = '';
  });

  function readFile(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload  = (e) => resolve(e.target.result);
      reader.onerror = () => reject();
      reader.readAsText(file);
    });
  }

  btnRemoveFile.addEventListener('click', () => {
    attachedFile = null;
    filePreview.classList.add('hidden');
    filePreviewName.textContent = '';
  });

  // ─── 4. INPUT HANDLING ────────────────────────────────────────────────────

  userInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (!isLoading) handleSend();
    }
  });

  userInput.addEventListener('input', () => {
    userInput.style.height = 'auto';
    userInput.style.height = Math.min(userInput.scrollHeight, 120) + 'px';
  });

  btnSend.addEventListener('click', () => { if (!isLoading) handleSend(); });

  btnClear.addEventListener('click', () => {
    conversationHistory = [];
    const extras = chatContainer.querySelectorAll('.message:not(:first-child), div[style]');
    extras.forEach(m => m.remove());
  });

  btnSettings.addEventListener('click', () => {
    chrome.runtime.openOptionsPage?.() ||
      window.open(chrome.runtime.getURL('settings/settings.html'));
  });

  // ─── 5. SEND ──────────────────────────────────────────────────────────────

  async function handleSend() {
    const text = userInput.value.trim();
    if (!text && !attachedFile) return;

    // Re-read storage in case user just saved keys
    const stored = await new Promise(r =>
      chrome.storage.local.get(
        ['sfAccessToken', 'sfInstanceUrl', 'backendUrl',
         'anthropicApiKey', 'openaiApiKey', 'googleApiKey', 'llmProvider'],
        r
      )
    );

    if (stored.backendUrl) BACKEND_URL = stored.backendUrl;
    llmProvider = stored.llmProvider || 'anthropic';
    apiKey      = stored[`${llmProvider}ApiKey`] || null;

    if (!apiKey) {
      showError('No API key configured. Click ⚙ to add your LLM API key.');
      return;
    }

    if (!stored.sfAccessToken || !stored.sfInstanceUrl) {
      showError('Not connected to Salesforce. Click ⚙ → Connect to Salesforce.');
      return;
    }

    // Update orgContext with latest token
    orgContext = {
      sessionId:    stored.sfAccessToken,
      instanceUrl:  stored.sfInstanceUrl,
      pageContext:  orgContext?.pageContext || {},
      isProbablyProduction: false,
    };

    const displayText  = text || `[File: ${attachedFile?.name}]`;
    appendUserMessage(displayText, attachedFile?.name);

    userInput.value = '';
    userInput.style.height = 'auto';

    const fileForRequest = attachedFile;
    attachedFile = null;
    filePreview.classList.add('hidden');

    conversationHistory.push({
      role:    'user',
      content: buildUserContent(text, fileForRequest),
    });

    setLoading(true);
    const typingEl = appendTypingIndicator();

    try {
      await streamResponse(fileForRequest, typingEl);
    } catch (err) {
      typingEl.remove();
      showError(`Request failed: ${err.message}`);
    } finally {
      setLoading(false);
    }
  }

  function buildUserContent(text, file) {
    if (!file) return text;
    return `${text}\n\n[Attached file: ${file.name}]\n\`\`\`\n${file.content?.slice(0, 8000)}\n\`\`\``;
  }

  // ─── 6. STREAMING ─────────────────────────────────────────────────────────

  async function streamResponse(file, typingEl) {
    const payload = {
      message:      conversationHistory[conversationHistory.length - 1].content,
      history:      conversationHistory.slice(0, -1),
      session_id:   orgContext.sessionId,
      instance_url: orgContext.instanceUrl,
      api_key:      apiKey,
      llm_provider: llmProvider,
      page_context: orgContext.pageContext,
      is_production: orgContext.isProbablyProduction,
    };

    if (file) {
      payload.file_name    = file.name;
      payload.file_content = file.content;
      payload.file_type    = file.type;
    }

    const response = await fetch(`${BACKEND_URL}/chat`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(payload),
    });

    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(err.detail || `HTTP ${response.status}`);
    }

    typingEl.remove();
    const { contentEl } = appendAssistantMessage('');

    const reader  = response.body.getReader();
    const decoder = new TextDecoder();
    let fullText  = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const chunk = decoder.decode(value, { stream: true });
      for (const line of chunk.split('\n')) {
        if (!line.startsWith('data: ')) continue;
        const data = line.slice(6).trim();
        if (data === '[DONE]') break;
        try {
          const parsed = JSON.parse(data);
          if (parsed.token) {
            fullText += parsed.token;
            contentEl.innerHTML = renderMarkdown(fullText);
            scrollToBottom();
          }
          if (parsed.error) throw new Error(parsed.error);
        } catch (e) {
          if (e.message && e.message !== 'Unexpected end of JSON input') {
            // only rethrow real errors
            if (e.message.startsWith('Unexpected')) continue;
            throw e;
          }
        }
      }
    }

    conversationHistory.push({ role: 'assistant', content: fullText });
  }

  // ─── 7. MARKDOWN ──────────────────────────────────────────────────────────

  function renderMarkdown(text) {
    return text
      .replace(/```(\w+)?\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>')
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/\*(.+?)\*/g, '<em>$1</em>')
      .replace(/^[-•]\s+(.+)$/gm, '<li>$1</li>')
      .replace(/^\d+\.\s+(.+)$/gm, '<li>$1</li>')
      .replace(/^### (.+)$/gm, '<h4>$1</h4>')
      .replace(/^## (.+)$/gm, '<h3>$1</h3>')
      .replace(/^# (.+)$/gm, '<h2>$1</h2>')
      .replace(/\n\n/g, '</p><p>')
      .replace(/\n/g, '<br/>');
  }

  // ─── 8. DOM HELPERS ───────────────────────────────────────────────────────

  function appendUserMessage(text, fileName) {
    const wrapper = document.createElement('div');
    wrapper.className = 'message user-message';
    const content = document.createElement('div');
    content.className = 'message-content';
    if (fileName) {
      const tag = document.createElement('div');
      tag.className = 'file-tag';
      tag.textContent = `📎 ${fileName}`;
      content.appendChild(tag);
    }
    const textNode = document.createElement('p');
    textNode.textContent = text;
    content.appendChild(textNode);
    wrapper.appendChild(content);
    chatContainer.appendChild(wrapper);
    scrollToBottom();
    return wrapper;
  }

  function appendAssistantMessage(text) {
    const wrapper = document.createElement('div');
    wrapper.className = 'message assistant-message';
    const avatar = document.createElement('div');
    avatar.className = 'message-avatar';
    avatar.textContent = '✦';
    const contentEl = document.createElement('div');
    contentEl.className = 'message-content';
    contentEl.innerHTML = text ? renderMarkdown(text) : '';
    wrapper.appendChild(avatar);
    wrapper.appendChild(contentEl);
    chatContainer.appendChild(wrapper);
    scrollToBottom();
    return { wrapper, contentEl };
  }

  function appendTypingIndicator() {
    const wrapper = document.createElement('div');
    wrapper.className = 'message assistant-message';
    const avatar = document.createElement('div');
    avatar.className = 'message-avatar';
    avatar.textContent = '✦';
    const content = document.createElement('div');
    content.className = 'message-content';
    content.innerHTML = `<div class="typing-indicator"><span></span><span></span><span></span></div>`;
    wrapper.appendChild(avatar);
    wrapper.appendChild(content);
    chatContainer.appendChild(wrapper);
    scrollToBottom();
    return wrapper;
  }

  function showError(message) {
    const wrapper = document.createElement('div');
    wrapper.className = 'message assistant-message error-message';
    const avatar = document.createElement('div');
    avatar.className = 'message-avatar';
    avatar.textContent = '✦';
    const content = document.createElement('div');
    content.className = 'message-content';
    content.textContent = `⚠ ${message}`;
    wrapper.appendChild(avatar);
    wrapper.appendChild(content);
    chatContainer.appendChild(wrapper);
    scrollToBottom();
  }

  function setLoading(state) {
    isLoading = state;
    btnSend.disabled = state;
    sendIcon.classList.toggle('hidden', state);
    loadingSpinner.classList.toggle('hidden', !state);
    userInput.disabled = state;
  }

  function scrollToBottom() {
    chatContainer.scrollTop = chatContainer.scrollHeight;
  }

  // ─── INIT ─────────────────────────────────────────────────────────────────
  init();

})();
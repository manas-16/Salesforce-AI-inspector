// ─── Salesforce (AI)nspector — sidebar.js ────────────────────────────────────

(function () {
  'use strict';

  let BACKEND_URL = 'http://127.0.0.1:8000';

  let orgContext          = null;
  let apiKey              = null;
  let llmProvider         = 'anthropic';
  let attachedFile        = null;
  let isLoading           = false;
  let conversationHistory = [];

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

  // ─── INIT ─────────────────────────────────────────────────────────────────

  async function init() {
    const stored = await getStored();
    if (stored.backendUrl) BACKEND_URL = normalizeUrl(stored.backendUrl);
    llmProvider = stored.llmProvider || 'anthropic';
    apiKey      = stored[`${llmProvider}ApiKey`] || null;
    if (!apiKey) showOrgBanner('error', 'No API key. Click ⚙ to configure.');

    if (stored.sfAccessToken && stored.sfInstanceUrl) {
      orgContext = {
        sessionId:            stored.sfAccessToken,
        instanceUrl:          stored.sfInstanceUrl,
        pageContext:          {},
        isProbablyProduction: false,
        treatAsSandbox:       Boolean(stored.sfTreatAsSandbox),
      };
      try {
        showOrgBanner('connected', `Connected · ${new URL(stored.sfInstanceUrl).hostname}`);
      } catch { showOrgBanner('connected', 'Connected'); }
    } else {
      showOrgBanner('error', 'Not connected. Click ⚙ → Connect to Salesforce.');
    }
    window.parent.postMessage({ type: 'REQUEST_CONTEXT' }, '*');
  }

  // ─── PAGE CONTEXT ─────────────────────────────────────────────────────────

  window.addEventListener('message', (event) => {
    if (event.data?.type !== 'SF_CONTEXT') return;
    const ctx            = event.data;
    const treatAsSandbox = orgContext?.treatAsSandbox || false;
    orgContext = {
      sessionId:            orgContext?.sessionId   || ctx.sessionId   || null,
      instanceUrl:          orgContext?.instanceUrl || ctx.instanceUrl || null,
      pageContext:          ctx.pageContext || orgContext?.pageContext  || {},
      isProbablyProduction: Boolean(ctx.isProbablyProduction) && !treatAsSandbox,
      treatAsSandbox,
    };
    if (orgContext.sessionId && orgContext.instanceUrl) {
      try {
        showOrgBanner('connected', `Connected · ${new URL(orgContext.instanceUrl).hostname}`);
      } catch { showOrgBanner('connected', 'Connected'); }
      prodWarning.classList.toggle('hidden', !orgContext.isProbablyProduction);
    }
    if (ctx.pageContext?.heading) appendContextHint(ctx.pageContext);
  });

  function showOrgBanner(status, text) {
    orgBanner.classList.remove('hidden', 'connected', 'error', 'connecting');
    orgBanner.classList.add(status);
    orgStatusText.textContent = text;
  }

  function appendContextHint(pageContext) {
    const hint = document.createElement('div');
    hint.className   = 'context-hint';
    hint.textContent = `📍 ${pageContext.heading || pageContext.url || ''}`;
    chatContainer.appendChild(hint);
  }

  // ─── FILE HANDLING ────────────────────────────────────────────────────────

  fileInput.addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const ext = '.' + file.name.split('.').pop().toLowerCase();
    if (!['.csv','.json','.cls','.xml','.xlsx','.txt'].includes(ext)) {
      showError('Unsupported file type.'); return;
    }
    if (file.size > 5 * 1024 * 1024) { showError('File too large. Max 5MB.'); return; }
    try {
      attachedFile = { name: file.name, content: await readFile(file), type: ext };
      filePreviewName.textContent = `📎 ${file.name}`;
      filePreview.classList.remove('hidden');
    } catch { showError('Failed to read file.'); }
    fileInput.value = '';
  });

  function readFile(file) {
    return new Promise((res, rej) => {
      const r = new FileReader();
      r.onload  = e => res(e.target.result);
      r.onerror = () => rej();
      r.readAsText(file);
    });
  }

  btnRemoveFile.addEventListener('click', () => {
    attachedFile = null;
    filePreview.classList.add('hidden');
    filePreviewName.textContent = '';
  });

  // ─── INPUT ────────────────────────────────────────────────────────────────

  userInput.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (!isLoading) handleSend(); }
  });
  userInput.addEventListener('input', () => {
    userInput.style.height = 'auto';
    userInput.style.height = Math.min(userInput.scrollHeight, 120) + 'px';
  });
  btnSend.addEventListener('click',  () => { if (!isLoading) handleSend(); });
  btnClear.addEventListener('click', () => {
    conversationHistory = [];
    chatContainer.querySelectorAll('.message:not(:first-child), .context-hint').forEach(m => m.remove());
  });
  btnSettings.addEventListener('click', () =>
    chrome.runtime.openOptionsPage?.() || window.open(chrome.runtime.getURL('settings/settings.html'))
  );

  // ─── SEND ─────────────────────────────────────────────────────────────────

  async function handleSend() {
    const text = userInput.value.trim();
    if (!text && !attachedFile) return;

    const stored = await getStored();
    if (stored.backendUrl) BACKEND_URL = normalizeUrl(stored.backendUrl);
    llmProvider = stored.llmProvider || 'anthropic';
    apiKey      = stored[`${llmProvider}ApiKey`] || null;
    if (!apiKey) { showError('No API key. Click ⚙ to configure.'); return; }

    const sessionId   = stored.sfAccessToken  || orgContext?.sessionId;
    const instanceUrl = stored.sfInstanceUrl  || orgContext?.instanceUrl;
    if (!sessionId || !instanceUrl) {
      showError('Not connected to Salesforce. Click ⚙ → Connect to Salesforce.');
      return;
    }
    orgContext = {
      sessionId, instanceUrl,
      pageContext:          orgContext?.pageContext || {},
      isProbablyProduction: Boolean(orgContext?.isProbablyProduction),
      treatAsSandbox:       Boolean(orgContext?.treatAsSandbox),
    };

    appendUserMessage(text, attachedFile?.name);
    userInput.value = '';
    userInput.style.height = 'auto';

    const fileForRequest = attachedFile;
    attachedFile = null;
    filePreview.classList.add('hidden');

    conversationHistory.push({ role: 'user', content: buildContent(text, fileForRequest) });
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

  function buildContent(text, file) {
    if (!file) return text;
    return `${text}\n\n[Attached file: ${file.name}]\n\`\`\`\n${(file.content||'').slice(0,8000)}\n\`\`\``;
  }

  // ─── STREAMING ────────────────────────────────────────────────────────────

  async function streamResponse(file, typingEl) {
    const payload = {
      message:          conversationHistory[conversationHistory.length - 1].content,
      history:          conversationHistory.slice(0, -1),
      session_id:       orgContext.sessionId,
      instance_url:     orgContext.instanceUrl,
      api_key:          apiKey,
      llm_provider:     llmProvider,
      page_context:     orgContext.pageContext,
      is_production:    orgContext.isProbablyProduction,
      treat_as_sandbox: Boolean(orgContext.treatAsSandbox),
    };
    if (file) { payload.file_name = file.name; payload.file_content = file.content; payload.file_type = file.type; }

    const response = await fetch(`${BACKEND_URL}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(err.detail || `HTTP ${response.status}`);
    }

    let contentEl     = null;
    let bubbleWrapper = null;
    let fullText      = '';
    let buffer        = '';
    const reader  = response.body.getReader();
    const decoder = new TextDecoder();

    try {
      outer: while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        let nl;
        while ((nl = buffer.indexOf('\n')) !== -1) {
          const line = buffer.slice(0, nl);
          buffer = buffer.slice(nl + 1);
          if (!line.startsWith('data: ')) continue;
          const data = line.slice(6).trim();
          if (data === '[DONE]') break outer;

          let parsed;
          try { parsed = JSON.parse(data); } catch { continue; }
          if (parsed.error) throw new Error(parsed.error);

          if (parsed.token) {
            fullText += parsed.token;
            if (!contentEl) {
              typingEl.remove();
              const msg     = appendAssistantMessage('');
              contentEl     = msg.contentEl;
              bubbleWrapper = msg.wrapper;
            }
            contentEl.innerHTML = renderMarkdown(fullText);
            scrollToBottom();
          }
        }
      }
    } catch (e) {
      bubbleWrapper ? bubbleWrapper.remove() : typingEl.remove();
      throw e;
    }

    if (!contentEl) {
      typingEl.remove();
      appendAssistantMessage('_(no response)_');
    }
    if (fullText) conversationHistory.push({ role: 'assistant', content: fullText });
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // MARKDOWN RENDERER
  // Since the system prompt guarantees markdown output, this renderer handles
  // the full spec: headings, tables, code blocks, lists, bold, italic, hr, etc.
  // ═══════════════════════════════════════════════════════════════════════════

  function renderMarkdown(raw) {
    // ── Step 1: extract fenced code blocks (protect from further processing)
    const codeBlocks = [];
    let text = raw.replace(/```([^\n]*)\n?([\s\S]*?)```/g, (_, lang, body) => {
      const i = codeBlocks.length;
      codeBlocks.push({ lang: lang.trim(), body });
      return `\x00CODE${i}\x00`;
    });

    // ── Step 2: split into logical blocks (double newline = block boundary)
    const blocks = text.split(/\n{2,}/);

    const html = blocks.map(block => {
      block = block.trim();
      if (!block) return '';

      // Restored code block
      if (/^\x00CODE\d+\x00$/.test(block)) {
        const i = parseInt(block.match(/\d+/)[0]);
        const { lang, body } = codeBlocks[i];
        return `<pre><code class="lang-${escHtml(lang)}">${escHtml(body.trimEnd())}</code></pre>`;
      }

      // Horizontal rule
      if (/^(-{3,}|\*{3,}|_{3,})$/.test(block)) return '<hr/>';

      // Headings ─ must check BEFORE paragraph
      const headingMatch = block.match(/^(#{1,6})\s+(.+)$/);
      if (headingMatch) {
        const level = Math.min(headingMatch[1].length + 1, 6); // h2–h6 (h1 looks too large in sidebar)
        return `<h${level}>${inlineMarkdown(headingMatch[2])}</h${level}>`;
      }

      // Markdown table  ─  | header | header |\n| --- | --- |\n| cell | cell |
      if (block.includes('|') && isTable(block)) return renderTable(block);

      // Unordered list
      if (/^[-*•]\s+/m.test(block)) return renderList(block, false);

      // Ordered list
      if (/^\d+\.\s+/m.test(block)) return renderList(block, true);

      // Blockquote
      if (/^>\s*/m.test(block)) {
        const inner = block.replace(/^>\s*/gm, '').trim();
        return `<blockquote>${inlineMarkdown(inner)}</blockquote>`;
      }

      // Default: paragraph (may contain inline code-block placeholder)
      const lines = block.split('\n').map(l => l.trim()).filter(Boolean);
      const inner = lines.map(inlineMarkdown).join('<br/>');
      return `<p>${inner}</p>`;

    }).join('');

    // ── Step 3: restore any inline code blocks missed by paragraph processing
    return html.replace(/\x00CODE(\d+)\x00/g, (_, i) => {
      const { lang, body } = codeBlocks[parseInt(i)];
      return `<pre><code class="lang-${escHtml(lang)}">${escHtml(body.trimEnd())}</code></pre>`;
    });
  }

  // ── Inline markdown (bold, italic, code, links, strikethrough) ─────────────
  function inlineMarkdown(text) {
    return escHtml(text)
      // inline code (must run on escaped HTML)
      .replace(/`([^`]+)`/g, (_, c) => `<code>${c}</code>`)
      // unescape for bold/italic (we need to work on the original chars)
      .replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>')
      .replace(/\*\*(.+?)\*\*/g,     '<strong>$1</strong>')
      .replace(/\*(.+?)\*/g,         '<em>$1</em>')
      .replace(/___(.+?)___/g,       '<strong><em>$1</em></strong>')
      .replace(/__(.+?)__/g,         '<strong>$1</strong>')
      .replace(/_(.+?)_/g,           '<em>$1</em>')
      .replace(/~~(.+?)~~/g,         '<del>$1</del>');
  }

  // ── Tables ──────────────────────────────────────────────────────────────────
  function isTable(block) {
    const lines = block.split('\n').map(l => l.trim()).filter(Boolean);
    return lines.length >= 2 && /^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?$/.test(lines[1]);
  }

  function renderTable(block) {
    const lines = block.split('\n').map(l => l.trim()).filter(Boolean);
    const headers = splitRow(lines[0]);
    const body    = lines.slice(2); // skip separator row

    let html = '<div class="md-table-wrap"><table class="md-table"><thead><tr>';
    headers.forEach(h => { html += `<th>${inlineMarkdown(h)}</th>`; });
    html += '</tr></thead><tbody>';
    body.forEach(line => {
      const cells = splitRow(line);
      html += '<tr>';
      cells.forEach(c => { html += `<td>${inlineMarkdown(c)}</td>`; });
      html += '</tr>';
    });
    html += '</tbody></table></div>';
    return html;
  }

  function splitRow(line) {
    const parts = line.split('|').map(c => c.trim());
    if (parts[0] === '') parts.shift();
    if (parts[parts.length - 1] === '') parts.pop();
    return parts;
  }

  // ── Lists ───────────────────────────────────────────────────────────────────
  function renderList(block, ordered) {
    const tag   = ordered ? 'ol' : 'ul';
    const items = block.split('\n')
      .map(l => l.trim())
      .filter(l => /^([-*•]|\d+\.)\s+/.test(l))
      .map(l => `<li>${inlineMarkdown(l.replace(/^([-*•]|\d+\.)\s+/, ''))}</li>`)
      .join('');
    return `<${tag}>${items}</${tag}>`;
  }

  // ── HTML escape ─────────────────────────────────────────────────────────────
  function escHtml(str) {
    return String(str ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // DOM HELPERS
  // ═══════════════════════════════════════════════════════════════════════════

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
    const p = document.createElement('p');
    p.textContent = text;
    content.appendChild(p);
    wrapper.appendChild(content);
    chatContainer.appendChild(wrapper);
    scrollToBottom();
    return wrapper;
  }

  function appendAssistantMessage(text) {
    const wrapper   = document.createElement('div');
    wrapper.className = 'message assistant-message';
    const avatar    = document.createElement('div');
    avatar.className  = 'message-avatar';
    avatar.textContent = '✦';
    const contentEl = document.createElement('div');
    contentEl.className = 'message-content';
    if (text) contentEl.innerHTML = renderMarkdown(text);
    wrapper.appendChild(avatar);
    wrapper.appendChild(contentEl);
    chatContainer.appendChild(wrapper);
    scrollToBottom();
    return { wrapper, contentEl };
  }

  function appendTypingIndicator() {
    const wrapper = document.createElement('div');
    wrapper.className = 'message assistant-message';
    const avatar  = document.createElement('div');
    avatar.className  = 'message-avatar';
    avatar.textContent = '✦';
    const content = document.createElement('div');
    content.className = 'message-content typing-wrap';
    content.innerHTML = '<div class="typing-indicator"><span></span><span></span><span></span></div>';
    wrapper.appendChild(avatar);
    wrapper.appendChild(content);
    chatContainer.appendChild(wrapper);
    scrollToBottom();
    return wrapper;
  }

  function showError(message) {
    const wrapper = document.createElement('div');
    wrapper.className = 'message assistant-message error-message';
    const avatar  = document.createElement('div');
    avatar.className  = 'message-avatar';
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
    isLoading          = state;
    btnSend.disabled   = state;
    userInput.disabled = state;
    sendIcon.classList.toggle('hidden', state);
    loadingSpinner.classList.toggle('hidden', !state);
  }

  function scrollToBottom() { chatContainer.scrollTop = chatContainer.scrollHeight; }

  // ─── UTILS ────────────────────────────────────────────────────────────────

  function getStored() {
    return new Promise(r =>
      chrome.storage.local.get(
        ['sfAccessToken','sfInstanceUrl','backendUrl','sfTreatAsSandbox',
         'anthropicApiKey','openaiApiKey','googleApiKey','llmProvider'], r
      )
    );
  }

  function normalizeUrl(url) {
    return (url || 'http://127.0.0.1:8000').trim().replace(/\/+$/, '')
      .replace('http://localhost:', 'http://127.0.0.1:');
  }

  init();
})();
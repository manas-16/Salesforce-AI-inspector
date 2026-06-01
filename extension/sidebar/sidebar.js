// ─── Salesforce (AI)nspector — sidebar.js ────────────────────────────────────

(function () {
  'use strict';

  let BACKEND_URL = 'http://127.0.0.1:8000';

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
         'anthropicApiKey', 'openaiApiKey', 'googleApiKey', 'llmProvider', 'sfTreatAsSandbox'],
        r
      )
    );

    // Backend URL
    if (stored.backendUrl) BACKEND_URL = normalizeBackendUrl(stored.backendUrl);

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
        treatAsSandbox:       Boolean(stored.sfTreatAsSandbox),
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
    const treatAsSandbox = orgContext?.treatAsSandbox || false;
    const detectedProduction = Boolean(ctx.isProbablyProduction);

    orgContext = {
      sessionId: orgContext?.sessionId || ctx.sessionId || null,
      instanceUrl: orgContext?.instanceUrl || ctx.instanceUrl || null,
      pageContext: ctx.pageContext || orgContext?.pageContext || {},
      isProbablyProduction: detectedProduction && !treatAsSandbox,
      treatAsSandbox,
    };

    if (orgContext.sessionId && orgContext.instanceUrl) {
      showOrgBanner('connected', `Connected · ${new URL(orgContext.instanceUrl).hostname}`);
      prodWarning.classList.toggle('hidden', !orgContext.isProbablyProduction);
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

    if (stored.backendUrl) BACKEND_URL = normalizeBackendUrl(stored.backendUrl);
    llmProvider = stored.llmProvider || 'anthropic';
    apiKey      = stored[`${llmProvider}ApiKey`] || null;

    if (!apiKey) {
      showError('No API key configured. Click ⚙ to add your LLM API key.');
      return;
    }

    const sessionId = stored.sfAccessToken || orgContext?.sessionId;
    const instanceUrl = stored.sfInstanceUrl || orgContext?.instanceUrl;

    if (!sessionId || !instanceUrl) {
      showError('Not connected to Salesforce. Click ⚙ → Connect to Salesforce.');
      return;
    }

    // Update orgContext with latest token
    orgContext = {
      sessionId,
      instanceUrl,
      pageContext:  orgContext?.pageContext || {},
      isProbablyProduction: Boolean(orgContext?.isProbablyProduction),
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

    const reader  = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer    = '';
    let fullText  = '';
    let contentEl = null;
    let assistantBubble = null;

    const createAssistantBubble = () => {
      typingEl.remove();
      const assistantMessage = appendAssistantMessage('');
      assistantBubble = assistantMessage.wrapper;
      return assistantMessage.contentEl;
    };

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        let newlineIndex;

        let gotDone = false;
      while ((newlineIndex = buffer.indexOf('\n')) !== -1) {
          const line = buffer.slice(0, newlineIndex);
          buffer = buffer.slice(newlineIndex + 1);

          if (!line.startsWith('data: ')) continue;
          const data = line.slice(6).trim();
          if (data === '[DONE]') {
            gotDone = true;
            break;
          }

          try {
            const parsed = JSON.parse(data);
            if (parsed.token) {
              fullText += parsed.token;
              if (!contentEl) contentEl = createAssistantBubble();
              const displayText = stripLeadingRawJson(fullText);
              contentEl.innerHTML = renderMarkdown(displayText);
              scrollToBottom();
            }
            if (parsed.error) throw new Error(parsed.error);
          } catch (e) {
            if (e.message && e.message !== 'Unexpected end of JSON input') {
              if (e.message.startsWith('Unexpected')) continue;
              throw e;
            }
          }
        }
        if (gotDone) break;
      }
    } catch (e) {
      if (assistantBubble) {
        assistantBubble.remove();
      } else {
        typingEl.remove();
      }
      throw e;
    }

    if (!contentEl) {
      typingEl.remove();
      const { contentEl: fallbackEl } = appendAssistantMessage('No response returned.');
      conversationHistory.push({ role: 'assistant', content: fallbackEl.textContent });
      return;
    }

    conversationHistory.push({ role: 'assistant', content: fullText });
  }

  // ─── 7. MARKDOWN ──────────────────────────────────────────────────────────

  function renderMarkdown(text) {
    const codeBlocks = [];
    const placeholder = '%%CODE_BLOCK_%d%%';

    const cleaned = text.replace(/```(\w+)?\n([\s\S]*?)```/g, (match, lang, content) => {
      const index = codeBlocks.length;
      codeBlocks.push({ lang, content });
      return placeholder.replace('%d', index);
    });

    const blocks = cleaned.split(/\n\n+/);
    const html = blocks.map((block) => {
      if (isMarkdownTable(block)) return renderMarkdownTable(block);
      if (isMarkdownList(block)) return renderMarkdownList(block);
      return renderMarkdownParagraph(block);
    }).join('');

    return restoreCodeBlocks(html, codeBlocks);
  }

  function stripLeadingRawJson(text) {
    const trimmed = text.trimStart();
    if (!trimmed.startsWith('{') && !trimmed.startsWith('[')) {
      return text;
    }

    let depth = 0;
    let inString = false;
    let escaped = false;

    for (let i = 0; i < trimmed.length; i += 1) {
      const ch = trimmed[i];

      if (inString) {
        if (escaped) {
          escaped = false;
        } else if (ch === '\\') {
          escaped = true;
        } else if (ch === '"') {
          inString = false;
        }
        continue;
      }

      if (ch === '"') {
        inString = true;
        continue;
      }

      if (ch === '{' || ch === '[') {
        depth += 1;
        continue;
      }

      if ((ch === '}' && depth > 0) || (ch === ']' && depth > 0)) {
        depth -= 1;
        if (depth === 0) {
          const remainder = trimmed.slice(i + 1).trimStart();
          if (remainder) {
            return remainder;
          }
          break;
        }
      }
    }

    return text;
  }

  function restoreCodeBlocks(html, codeBlocks) {
    return html.replace(/%%CODE_BLOCK_(\d+)%%/g, (_, index) => {
      const block = codeBlocks[Number(index)];
      if (!block) return '';
      return `<pre><code>${escapeHtml(block.content)}</code></pre>`;
    });
  }

  function isMarkdownTable(block) {
    const lines = block.trim().split('\n').map((line) => line.trim()).filter(Boolean);
    return lines.length > 1 && lines[0].includes('|') && /^\|?\s*:?[-]+:?(\s*\|\s*:?-+:?\s*)+\|?$/.test(lines[1]);
  }

  function renderMarkdownTable(block) {
    const lines = block.trim().split('\n').map((line) => line.trim()).filter(Boolean);
    const header = splitTableRow(lines[0]);
    const rows = lines.slice(2).map(splitTableRow);

    const headerHtml = header.map((cell) => `<th>${escapeHtml(cell)}</th>`).join('');
    const rowsHtml = rows.map((row) => `
      <tr>${row.map((cell) => `<td>${renderInlineMarkdown(cell)}</td>`).join('')}</tr>
    `).join('');

    return `
      <div class="markdown-table-wrapper">
        <table class="markdown-table">
          <thead><tr>${headerHtml}</tr></thead>
          <tbody>${rowsHtml}</tbody>
        </table>
      </div>
    `;
  }

  function splitTableRow(line) {
    const cells = line.split('|').map((cell) => cell.trim());
    if (cells[0] === '') cells.shift();
    if (cells[cells.length - 1] === '') cells.pop();
    return cells;
  }

  function isMarkdownList(block) {
    return block.split('\n').every((line) => /^\s*([-*•]|\d+\.)\s+/.test(line));
  }

  function renderMarkdownList(block) {
    const lines = block.split('\n');
    const ordered = /^\s*\d+\./.test(lines[0]);
    const tag = ordered ? 'ol' : 'ul';
    const items = lines.map((line) => {
      const content = line.replace(/^\s*([-*•]|\d+\.)\s+/, '').trim();
      return `<li>${renderInlineMarkdown(content)}</li>`;
    }).join('');

    return `<${tag}>${items}</${tag}>`;
  }

  function renderMarkdownParagraph(block) {
    const text = block.trim();
    if (!text) return '';
    const lines = text.split('\n');
    const inner = lines.map((line) => renderInlineMarkdown(line.trim())).join('<br/>');
    return `<p>${inner}</p>`;
  }

  function renderInlineMarkdown(text) {
    return escapeHtml(text)
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/\*(.+?)\*/g, '<em>$1</em>');
  }

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
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
    adjustMessageSizing(content);
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
    adjustMessageSizing(contentEl);
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
  
  // Reduce font-size / apply long-message styling when content is large
  function adjustMessageSizing(contentEl) {
    if (!contentEl) return;
    const txt = (contentEl.textContent || '').trim();
    const longWord = txt.split(/\s+/).some(w => w.length > 60);
    if (txt.length > 240 || longWord) {
      contentEl.classList.add('long');
    } else {
      contentEl.classList.remove('long');
    }
  }
  function normalizeBackendUrl(url) {
    const normalized = (url || 'http://127.0.0.1:8000').trim().replace(/\/+$/, '');
    return normalized.replace('http://localhost:', 'http://127.0.0.1:');
  }

  // ─── INIT ─────────────────────────────────────────────────────────────────
  init();

})();

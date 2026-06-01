// ─── Salesforce (AI)nspector — settings.js ───────────────────────────────────

(function () {
  'use strict';

  const DEFAULT_BACKEND_URL = 'http://127.0.0.1:8000';

  const PROVIDER_META = {
    anthropic: {
      placeholder: 'sk-ant-api03-...',
      hint:        'starts with sk-ant-...',
      link:        'https://console.anthropic.com/account/keys',
      linkText:    'console.anthropic.com',
      validate:    (k) => k.startsWith('sk-ant-'),
      validationMsg: 'Invalid key. Anthropic keys start with sk-ant-',
    },
    openai: {
      placeholder: 'sk-...',
      hint:        'starts with sk-...',
      link:        'https://platform.openai.com/api-keys',
      linkText:    'platform.openai.com',
      validate:    (k) => k.startsWith('sk-'),
      validationMsg: 'Invalid key. OpenAI keys start with sk-',
    },
    google: {
      placeholder: 'AIza... or AQ...',
      hint:        'from Google AI Studio',
      link:        'https://aistudio.google.com/app/apikey',
      linkText:    'aistudio.google.com',
      validate:    (k) => k.length > 20,  // just check length, no prefix check
      validationMsg: 'Key too short — paste the full key from AI Studio',
    },
  };

  // ─── DOM REFS ───────────────────────────────────────────────────────────────
  const providerSelect      = document.getElementById('provider-select');
  const apiKeyInput         = document.getElementById('api-key-input');
  const keyHint             = document.getElementById('key-hint');
  const providerLink        = document.getElementById('provider-link');
  const btnSaveKey          = document.getElementById('btn-save-key');
  const btnClearKey         = document.getElementById('btn-clear-key');
  const keyStatusMsg        = document.getElementById('key-status-msg');
  const keyStatus           = document.getElementById('key-status');
  const keyStatusText       = document.getElementById('key-status-text');

  const consumerKeyInput    = document.getElementById('consumer-key');
  const consumerSecretInput = document.getElementById('consumer-secret');
  const sfUsernameInput     = document.getElementById('sf-username');
  const sfPasswordInput     = document.getElementById('sf-password');
  const sfTokenInput        = document.getElementById('sf-security-token');
  const isSandboxCheckbox   = document.getElementById('is-sandbox');
  const treatAsSandboxCheckbox = document.getElementById('treat-as-sandbox');
  const btnSfLogin          = document.getElementById('btn-sf-login');
  const btnSfDisconnect     = document.getElementById('btn-sf-disconnect');
  const sfLoginStatus       = document.getElementById('sf-login-status');
  const sfConnectedBadge    = document.getElementById('sf-connected-badge');
  const sfConnectedText     = document.getElementById('sf-connected-text');

  const backendUrlInput     = document.getElementById('backend-url-input');
  const btnSaveBackend      = document.getElementById('btn-save-backend');
  const btnTestBackend      = document.getElementById('btn-test-backend');
  const backendStatusMsg    = document.getElementById('backend-status-msg');

  // ─── INIT ───────────────────────────────────────────────────────────────────

  function init() {
    chrome.storage.local.get(
      ['llmProvider', 'anthropicApiKey', 'openaiApiKey', 'googleApiKey',
       'backendUrl', 'sfAccessToken', 'sfInstanceUrl',
       'sfUsername', 'sfConsumerKey', 'sfIsSandbox', 'sfTreatAsSandbox'],
      (result) => {
        // LLM provider
        const savedProvider = result.llmProvider || 'anthropic';
        providerSelect.value = savedProvider;
        updateProviderUI(savedProvider);
        const savedKey = result[`${savedProvider}ApiKey`];
        if (savedKey) {
          apiKeyInput.value = savedKey;
          setKeyStatus('saved', 'API key saved');
        } else {
          setKeyStatus('missing', 'No key saved');
        }

        // Backend URL
        backendUrlInput.value = normalizeBackendUrl(result.backendUrl);

        // Salesforce connection
        if (result.sfAccessToken && result.sfInstanceUrl) {
          showConnectedBadge(result.sfInstanceUrl);
          // Pre-fill saved username/consumer key for reference
          if (result.sfUsername)     sfUsernameInput.value     = result.sfUsername;
          if (result.sfConsumerKey)  consumerKeyInput.value    = result.sfConsumerKey;
        }

        // Sandbox options
        isSandboxCheckbox.checked = Boolean(result.sfIsSandbox);
        treatAsSandboxCheckbox.checked = Boolean(result.sfTreatAsSandbox);
      }
    );
  }

  // ─── SALESFORCE OAUTH LOGIN ─────────────────────────────────────────────────

  btnSfLogin.addEventListener('click', async () => {
    const backendUrl = normalizeBackendUrl(backendUrlInput.value);

    const body = {
      consumer_key:    consumerKeyInput.value.trim(),
      consumer_secret: consumerSecretInput.value.trim(),
      is_sandbox:      isSandboxCheckbox.checked,
    };

    if (!body.consumer_key) {
      showStatus(sfLoginStatus, 'error', 'Please fill in the Consumer Key.');
      return;
    }

    showStatus(sfLoginStatus, 'success', 'Opening Salesforce login...');
    btnSfLogin.disabled = true;

    try {
      const response = await chrome.runtime.sendMessage({
        type: 'START_SF_OAUTH',
        payload: {
          backendUrl,
          requestBody: body,
        },
      });

      if (!response?.ok) {
        throw new Error(response?.error || 'OAuth failed.');
      }

      showConnectedBadge(response.result.instance_url);
      showStatus(sfLoginStatus, 'success', `Connected to ${response.result.instance_url}`);
      btnSfLogin.disabled = false;
      consumerSecretInput.value = '';

    } catch (err) {
      showStatus(sfLoginStatus, 'error', `Login failed: ${err.message}`);
      btnSfLogin.disabled = false;
    }
  });

  btnSfDisconnect.addEventListener('click', () => {
    chrome.storage.local.remove(
      ['sfAccessToken', 'sfRefreshToken', 'sfInstanceUrl', 'sfUsername', 'sfConsumerKey', 'sfIsSandbox', 'sfTreatAsSandbox'],
      () => {
        sfConnectedBadge.classList.remove('visible');
        consumerKeyInput.value    = '';
        consumerSecretInput.value = '';
        sfUsernameInput.value     = '';
        sfPasswordInput.value     = '';
        sfTokenInput.value        = '';
        showStatus(sfLoginStatus, 'success', 'Disconnected.');
      }
    );
  });

  function showConnectedBadge(instanceUrl) {
    try {
      sfConnectedText.textContent = `Connected · ${new URL(instanceUrl).hostname}`;
    } catch {
      sfConnectedText.textContent = 'Connected';
    }
    sfConnectedBadge.classList.add('visible');
  }

  // ─── PROVIDER SWITCH ────────────────────────────────────────────────────────

  providerSelect.addEventListener('change', () => {
    const provider = providerSelect.value;
    updateProviderUI(provider);
    chrome.storage.local.get([`${provider}ApiKey`], (result) => {
      const key = result[`${provider}ApiKey`] || '';
      apiKeyInput.value = key;
      setKeyStatus(key ? 'saved' : 'missing', key ? 'API key saved' : 'No key saved');
    });
    chrome.storage.local.set({ llmProvider: provider });
  });

  function updateProviderUI(provider) {
    const meta = PROVIDER_META[provider];
    apiKeyInput.placeholder = meta.placeholder;
    apiKeyInput.type        = 'password';
    keyHint.textContent     = meta.hint;
    providerLink.href       = meta.link;
    providerLink.textContent= meta.linkText;
  }

  // ─── API KEY ────────────────────────────────────────────────────────────────

  btnSaveKey.addEventListener('click', () => {
    const key      = apiKeyInput.value.trim();
    const provider = providerSelect.value;
    const meta     = PROVIDER_META[provider];

    if (!key) { showStatus(keyStatusMsg, 'error', 'Please enter an API key.'); return; }
    if (!meta.validate(key)) { showStatus(keyStatusMsg, 'error', meta.validationMsg); return; }

    chrome.storage.local.set({ [`${provider}ApiKey`]: key, llmProvider: provider }, () => {
      setKeyStatus('saved', 'API key saved');
      showStatus(keyStatusMsg, 'success', `✓ ${provider} API key saved.`);
    });
  });

  btnClearKey.addEventListener('click', () => {
    chrome.storage.local.remove(`${providerSelect.value}ApiKey`, () => {
      apiKeyInput.value = '';
      setKeyStatus('missing', 'No key saved');
      showStatus(keyStatusMsg, 'success', 'API key cleared.');
    });
  });

  // ─── BACKEND URL ────────────────────────────────────────────────────────────

  btnSaveBackend.addEventListener('click', () => {
    const url = normalizeBackendUrl(backendUrlInput.value);
    if (!url) { showStatus(backendStatusMsg, 'error', 'Please enter a URL.'); return; }
    try { new URL(url); } catch { showStatus(backendStatusMsg, 'error', 'Invalid URL.'); return; }
    chrome.storage.local.set({ backendUrl: url }, () => {
      showStatus(backendStatusMsg, 'success', '✓ Backend URL saved.');
    });
  });

  btnTestBackend.addEventListener('click', async () => {
    const url = normalizeBackendUrl(backendUrlInput.value);
    showStatus(backendStatusMsg, 'success', 'Testing...');
    try {
      const r = await fetch(`${url}/health`);
      if (r.ok) {
        const d = await r.json();
        showStatus(backendStatusMsg, 'success', `✓ Connected. Version: ${d.version}`);
      } else {
        showStatus(backendStatusMsg, 'error', `HTTP ${r.status}`);
      }
    } catch {
      showStatus(backendStatusMsg, 'error', 'Could not reach backend. Is it running?');
    }
  });

  treatAsSandboxCheckbox.addEventListener('change', () => {
    chrome.storage.local.set({ sfTreatAsSandbox: treatAsSandboxCheckbox.checked });
  });

  // ─── TOGGLE VISIBILITY (all fields) ─────────────────────────────────────────

  document.querySelectorAll('.toggle-visibility').forEach(btn => {
    btn.addEventListener('click', () => {
      const targetId = btn.getAttribute('data-target');
      const input    = document.getElementById(targetId);
      if (!input) return;
      const isPassword = input.type === 'password';
      input.type       = isPassword ? 'text' : 'password';
      btn.textContent  = isPassword ? '🙈' : '👁';
    });
  });

  // ─── HELPERS ────────────────────────────────────────────────────────────────

  function setKeyStatus(state, text) {
    keyStatus.className    = `key-status ${state}`;
    keyStatusText.textContent = text;
  }

  function showStatus(el, type, message) {
    el.className    = `status-msg ${type}`;
    el.textContent  = message;
    if (type === 'success') {
      setTimeout(() => { el.className = 'status-msg'; el.textContent = ''; }, 4000);
    }
  }

  function normalizeBackendUrl(url) {
    const normalized = (url || DEFAULT_BACKEND_URL).trim().replace(/\/+$/, '');
    return normalized.replace('http://localhost:', 'http://127.0.0.1:');
  }

  init();

})();

// ─── Salesforce (AI)nspector — content.js ────────────────────────────────────
// Runs inside every Salesforce page.
// Responsibilities:
//   1. Extract sessionId + instanceUrl from page context
//   2. Detect org type (sandbox vs production) via URL heuristics
//   3. Inject the sidebar iframe into the page
//   4. Act as message bridge between sidebar and background service worker

(function () {
  'use strict';

  const SIDEBAR_ID = 'sfai-sidebar-container';
  const SIDEBAR_WIDTH = '420px';

  // ─── 1. SESSION EXTRACTION ─────────────────────────────────────────────────

  function getSessionId() {
    const url = getInstanceUrl();

    if (chrome.cookies && chrome.cookies.getAll) {
      return new Promise((resolve) => {
        chrome.cookies.getAll({ url }, (cookies) => {
          if (cookies && cookies.length) {
            const sidCookie = cookies.find((c) => c.name === 'sid');
            if (sidCookie?.value) {
              return resolve(sidCookie.value);
            }

            const altCookie = cookies.find((c) => c.name.toLowerCase().includes('sid'));
            if (altCookie?.value) {
              return resolve(altCookie.value);
            }
          }

          chrome.cookies.getAll({ name: 'sid' }, (sidCookies) => {
            if (sidCookies && sidCookies.length) {
              const sidCookie = sidCookies.find((c) => c.value);
              if (sidCookie?.value) {
                return resolve(sidCookie.value);
              }
            }

            const sidCookie = document.cookie
              .split(';')
              .map((c) => c.trim())
              .find((c) => c.startsWith('sid='));

            if (sidCookie) return resolve(sidCookie.split('=')[1]);

            const altCookie = document.cookie
              .split(';')
              .map((c) => c.trim())
              .find((c) => c.includes('sid'));

            if (altCookie) return resolve(altCookie.split('=').slice(1).join('='));

            resolve(null);
          });
        });
      });
    }

    const sidCookie = document.cookie
      .split(';')
      .map((c) => c.trim())
      .find((c) => c.startsWith('sid='));

    if (sidCookie) return sidCookie.split('=')[1];

    const altCookie = document.cookie
      .split(';')
      .map((c) => c.trim())
      .find((c) => c.includes('sid'));

    if (altCookie) return altCookie.split('=').slice(1).join('=');

    return null;
  }

  function getInstanceUrl() {
    const origin = window.location.origin;
    // Convert Lightning URL to REST API URL
    // trailblaze.lightning.force.com -> trailblaze.my.salesforce.com
    return origin
      .replace('.lightning.force.com', '.my.salesforce.com')
      .replace('.lightning.salesforce.com', '.my.salesforce.com');
  }

  function getPageContext() {
    return {
      url: window.location.href,
      title: document.title,
      // Capture breadcrumb text if available in DOM
      breadcrumb: Array.from(
        document.querySelectorAll(
          '.slds-breadcrumb__item, .breadcrumb, [class*="breadcrumb"]'
        )
      )
        .map(el => el.innerText?.trim())
        .filter(Boolean)
        .join(' > '),

      // Capture any visible error messages
      errorMessages: Array.from(
        document.querySelectorAll(
          '[class*="error"], [class*="Error"], .forceFormMessage'
        )
      )
        .map(el => el.innerText?.trim())
        .filter(Boolean)
        .slice(0, 3), // max 3 errors

      // Visible page heading
      heading: document.querySelector(
        'h1, .slds-page-header__title, [class*="pageTitle"]'
      )?.innerText?.trim() || null,
    };
  }

  // ─── 2. ORG TYPE DETECTION ─────────────────────────────────────────────────
  // Heuristic only — auth.py on backend does the definitive check via API.
  // This is used to show an early warning before any API call is made.

  function isProbablyProduction() {
    const hostname = window.location.hostname;
    // Sandboxes typically have patterns like: myorg--uat.sandbox.my.salesforce.com
    // or myorg--dev.lightning.force.com
    const sandboxPatterns = [
      /--\w+\.sandbox\.my\.salesforce\.com/,
      /--\w+\.lightning\.force\.com/,
      /--\w+\.my\.salesforce\.com/,
      /\.sandbox\./,
    ];
    return !sandboxPatterns.some(p => p.test(hostname));
  }

  // ─── 3. SIDEBAR INJECTION ──────────────────────────────────────────────────

  function injectSidebar() {
    if (document.getElementById(SIDEBAR_ID)) return; // already injected

    // Outer container — pushes page content left, sits on right
    const container = document.createElement('div');
    container.id = SIDEBAR_ID;
    container.style.cssText = `
      position: fixed;
      top: 0;
      right: 0;
      width: 0;
      height: 100vh;
      z-index: 999999;
      transition: width 0.25s ease;
      box-shadow: -4px 0 16px rgba(0,0,0,0.15);
      background: #fff;
      border-left: 1px solid #ddd;
    `;

    // Toggle button
    const toggleBtn = document.createElement('button');
    toggleBtn.id = 'sfai-toggle-btn';
    toggleBtn.innerText = '✦ AI';
    toggleBtn.style.cssText = `
      position: fixed;
      top: 50%;
      right: 0;
      transform: translateY(-50%);
      z-index: 1000000;
      background: #1b4f8a;
      color: #fff;
      border: none;
      padding: 12px 8px;
      border-radius: 6px 0 0 6px;
      cursor: pointer;
      font-size: 12px;
      font-weight: bold;
      font-family: Arial, sans-serif;
      writing-mode: vertical-rl;
      text-orientation: mixed;
      letter-spacing: 1px;
      box-shadow: -2px 0 8px rgba(0,0,0,0.2);
    `;

    // Iframe — loads the sidebar UI
    const iframe = document.createElement('iframe');
    iframe.id = 'sfai-sidebar-iframe';
    iframe.src = chrome.runtime.getURL('sidebar/sidebar.html');
    iframe.style.cssText = `
      width: 100%;
      height: 100%;
      border: none;
    `;

    container.appendChild(iframe);
    document.body.appendChild(container);
    document.body.appendChild(toggleBtn);

    let isOpen = false;

    toggleBtn.addEventListener('click', () => {
      isOpen = !isOpen;
      container.style.width = isOpen ? SIDEBAR_WIDTH : '0';
      toggleBtn.style.right = isOpen ? SIDEBAR_WIDTH : '0';

      if (isOpen) {
        // Send session context to sidebar as soon as it opens
        sendContextToSidebar();
      }
    });
  }

  // ─── 4. CONTEXT RELAY TO SIDEBAR ───────────────────────────────────────────

  async function sendContextToSidebar() {
    const iframe = document.getElementById('sfai-sidebar-iframe');
    if (!iframe) return;

    const sessionId = await getSessionId();
    const context = {
      type: 'SF_CONTEXT',
      sessionId,
      instanceUrl: getInstanceUrl(),
      pageContext: getPageContext(),
      isProbablyProduction: isProbablyProduction(),
    };

    // Post message to iframe
    iframe.contentWindow?.postMessage(context, '*');
  }

  // ─── 5. MESSAGE BRIDGE ─────────────────────────────────────────────────────
  // Sidebar iframe -> content.js -> background.js (if needed for storage access)

  window.addEventListener('message', (event) => {
    // Only accept messages from our own extension
    if (event.source !== document.getElementById('sfai-sidebar-iframe')?.contentWindow) return;

    if (event.data?.type === 'REQUEST_CONTEXT') {
      sendContextToSidebar();
    }
  });

  // Listen for messages from background service worker
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.type === 'GET_SESSION') {
      sendResponse({
        sessionId: getSessionId(),
        instanceUrl: getInstanceUrl(),
        pageContext: getPageContext(),
        isProbablyProduction: isProbablyProduction(),
      });
    }
  });

  // ─── INIT ──────────────────────────────────────────────────────────────────

  // Wait for Salesforce Lightning to fully hydrate before injecting
  if (document.readyState === 'complete') {
    injectSidebar();
  } else {
    window.addEventListener('load', injectSidebar);
  }

})();
// CUI // SP-CTI
// STRATEGOS Intelligence Chat — floating panel powered by ChatManager.
// Reuses /api/chat/* (ChatManager) via thin Strategos proxy routes.
// Exposes: window.openStratChat(entityCtx?), window.sgChat = {open, close, showMenu}

(function () {
  'use strict';

  // ── State ──────────────────────────────────────────────────────────────
  let _ctxId        = sessionStorage.getItem('sg_ctx_id') || null;
  let _lastVersion  = 0;
  let _polling      = null;
  let _pendingEntity = null;
  let _isOpen       = false;
  let _isMinimized  = false;
  let _menuEntity   = null;  // entity stored for context menu click

  // ── DOM refs (populated after DOMContentLoaded) ────────────────────────
  let panel, fab, messages, input, sendBtn, typing, entityBadge, entityType,
      entityLabel, ctxMenu, ctxAsk, ctxDismiss, minimizeBtn, closeBtn, welcomeMsg;

  function _init() {
    panel       = document.getElementById('sg-chat-panel');
    fab         = document.getElementById('sg-chat-fab');
    messages    = document.getElementById('sg-chat-messages');
    input       = document.getElementById('sg-chat-input');
    sendBtn     = document.getElementById('sg-chat-send');
    typing      = document.getElementById('sg-typing');
    entityBadge = document.getElementById('sg-entity-badge');
    entityType  = document.getElementById('sg-entity-type');
    entityLabel = document.getElementById('sg-entity-label');
    ctxMenu     = document.getElementById('sg-ctx-menu');
    ctxAsk      = document.getElementById('sg-ctx-ask');
    ctxDismiss  = document.getElementById('sg-ctx-dismiss');
    minimizeBtn = document.getElementById('sg-chat-minimize');
    closeBtn    = document.getElementById('sg-chat-close');
    welcomeMsg  = document.getElementById('sg-welcome-msg');

    if (!panel) return;  // panel not present on this page

    // Bind controls
    fab.addEventListener('click', () => openPanel());
    closeBtn.addEventListener('click', () => closePanel());
    minimizeBtn.addEventListener('click', () => toggleMinimize());
    sendBtn.addEventListener('click', () => sendMessage());
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
    });

    // Context menu
    ctxAsk.addEventListener('click', () => { const ent = _menuEntity; hideCtxMenu(); if (ent) openPanel(ent); });
    ctxDismiss.addEventListener('click', hideCtxMenu);
    document.addEventListener('click', e => {
      if (ctxMenu && !ctxMenu.contains(e.target)) hideCtxMenu();
    });

    // Keyboard dismiss
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape') { closePanel(); hideCtxMenu(); }
    });

    // Restore existing context on page load
    if (_ctxId) _startPolling();
  }

  // ── Panel open/close ───────────────────────────────────────────────────
  function openPanel(entityCtx) {
    if (!panel) return;
    _isOpen = true;
    _isMinimized = false;
    panel.classList.add('sg-panel-open');
    panel.classList.remove('sg-minimized');
    fab.classList.add('sg-panel-open');
    fab.textContent = '⚡ STRATEGOS';

    // Show badge immediately — don't wait for inject round-trip
    if (entityCtx) _showEntityBadge(entityCtx);

    if (!_ctxId) {
      _pendingEntity = entityCtx || null;
      // Disable input until context is ready
      if (input) {
        input.disabled = true;
        input.placeholder = 'Connecting to STRATEGOS…';
      }
      if (sendBtn) sendBtn.disabled = true;
      _initContext();
    } else if (entityCtx) {
      _injectEntity(entityCtx);
    } else {
      // Panel already ready — focus input so user can type immediately
      if (input) input.focus();
    }
  }

  function closePanel() {
    if (!panel) return;
    _isOpen = false;
    panel.classList.remove('sg-panel-open', 'sg-minimized');
    fab.classList.remove('sg-panel-open');
    fab.innerHTML = '<span>⚡</span> STRATEGOS';
  }

  function toggleMinimize() {
    if (!panel) return;
    _isMinimized = !_isMinimized;
    panel.classList.toggle('sg-minimized', _isMinimized);
  }

  // ── Context lifecycle ─────────────────────────────────────────────────
  async function _initContext() {
    const page = document.title || window.location.pathname;
    try {
      const res = await fetch('/api/strategos/chat/init', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: 'local', page }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      _ctxId = data.context_id;
      sessionStorage.setItem('sg_ctx_id', _ctxId);
      _startPolling();
      if (_pendingEntity) { await _injectEntity(_pendingEntity); _pendingEntity = null; }
      // Context ready — unlock input and focus
      if (input) {
        input.disabled = false;
        input.placeholder = 'Ask STRATEGOS… (Enter to send)';
        input.focus();
      }
      if (sendBtn) sendBtn.disabled = false;
    } catch (err) {
      console.error('[STRATEGOS Chat] init failed:', err);
      // Restore input so user can retry or see the error
      if (input) {
        input.disabled = false;
        input.placeholder = 'Ask STRATEGOS… (Enter to send)';
      }
      if (sendBtn) sendBtn.disabled = false;
      _appendSystemMsg('Connection to STRATEGOS unavailable. Check server status.');
    }
  }

  // ── Message send ───────────────────────────────────────────────────────
  async function sendMessage() {
    if (!_ctxId || !input) return;
    const content = input.value.trim();
    if (!content) return;

    input.value = '';
    sendBtn.disabled = true;
    _appendMsg('user', content);
    _showTyping(true);

    try {
      let res = await fetch(`/api/strategos/chat/${_ctxId}/send`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content }),
      });
      // Context lost after server restart — re-init and retry once
      if (res.status === 404) {
        console.warn('[STRATEGOS Chat] context stale, re-initialising…');
        const initRes = await fetch('/api/strategos/chat/init', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
        });
        if (initRes.ok) {
          const initData = await initRes.json();
          _ctxId = initData.context_id;
          _startPolling();
          res = await fetch(`/api/strategos/chat/${_ctxId}/send`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content }),
          });
        }
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
    } catch (err) {
      console.error('[STRATEGOS Chat] send failed:', err);
      _appendSystemMsg('Message delivery failed. Try again.');
      _showTyping(false);
    } finally {
      sendBtn.disabled = false;
    }
  }

  // ── Entity injection ───────────────────────────────────────────────────
  async function _injectEntity(entity) {
    _showEntityBadge(entity);
    _showTyping(true);
    try {
      await fetch(`/api/strategos/chat/${_ctxId}/inject`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ entity }),
      });
    } catch (err) {
      console.error('[STRATEGOS Chat] inject failed:', err);
    }
  }

  // ── Polling ────────────────────────────────────────────────────────────
  function _startPolling() {
    if (_polling) clearInterval(_polling);
    _polling = setInterval(_pollState, 2000);
    _pollState();
  }

  async function _pollState() {
    if (!_ctxId) return;
    try {
      const res = await fetch(`/api/strategos/chat/poll/${_ctxId}`);
      if (!res.ok) return;
      const data = await res.json();

      if (data.dirty_version > _lastVersion) {
        _lastVersion = data.dirty_version;
        _renderMessages(data.messages || []);
      }
      _showTyping(data.is_processing || false);
    } catch (_) { /* silent — server may be restarting */ }
  }

  // ── Rendering ─────────────────────────────────────────────────────────
  function _renderMessages(msgs) {
    if (!messages) return;
    // Remove welcome message once we have real messages
    if (msgs.length > 0 && welcomeMsg) welcomeMsg.remove();

    // Only re-render if count changed (simple dirty check)
    const existing = messages.querySelectorAll('.sgc-msg').length;
    if (existing === msgs.length) return;

    // Clear and re-render
    const nodes = messages.querySelectorAll('.sgc-msg');
    nodes.forEach(n => n.remove());

    msgs.forEach(m => _appendMsg(m.role, m.content, false));
    messages.scrollTop = messages.scrollHeight;
  }

  function _appendMsg(role, content, scroll = true) {
    if (!messages) return;
    if (welcomeMsg && welcomeMsg.parentNode) welcomeMsg.remove();

    const wrap = document.createElement('div');
    wrap.className = `sgc-msg sgc-msg-${role === 'intervention' ? 'user' : role}`;

    const roleLabel = document.createElement('div');
    roleLabel.className = 'sgc-msg-role';
    roleLabel.textContent = role === 'user' ? 'You' : role === 'assistant' ? 'STRATEGOS' : role.toUpperCase();

    const bubble = document.createElement('div');
    bubble.className = 'sgc-bubble';

    // nav-sec-07: assistant content is LLM output. marked.parse emits raw HTML,
    // so it MUST be sanitized before innerHTML. DOMPurify is vendored locally
    // (static/vendor/dompurify) and loaded on Strategos pages via base.html. If it
    // is somehow unavailable, fail safe by rendering as plain text (textContent).
    if (role === 'assistant' && typeof marked !== 'undefined' && typeof DOMPurify !== 'undefined') {
      bubble.innerHTML = DOMPurify.sanitize(marked.parse(content || ''));
    } else {
      bubble.textContent = content || '';
    }

    wrap.appendChild(roleLabel);
    wrap.appendChild(bubble);
    messages.appendChild(wrap);

    if (scroll) messages.scrollTop = messages.scrollHeight;
  }

  function _appendSystemMsg(text) {
    _appendMsg('system', text);
  }

  function _showTyping(show) {
    if (!typing) return;
    typing.classList.toggle('visible', !!show);
  }

  // ── Entity badge ───────────────────────────────────────────────────────
  function _showEntityBadge(entity) {
    if (!entityBadge) return;
    const type = (entity.type || 'entity').replace('_', ' ').toUpperCase();
    const label = entity.label || entity.name || entity.mmsi || entity.id || '—';
    entityType.textContent = type;
    entityLabel.textContent = label;
    entityBadge.classList.add('visible');
  }

  function _hideEntityBadge() {
    if (entityBadge) entityBadge.classList.remove('visible');
  }

  // ── Context menu ───────────────────────────────────────────────────────
  function showCtxMenu(x, y, entity) {
    if (!ctxMenu) return;
    _menuEntity = entity;
    ctxMenu.style.left = Math.min(x, window.innerWidth - 220) + 'px';
    ctxMenu.style.top  = Math.min(y, window.innerHeight - 80) + 'px';
    ctxMenu.classList.add('visible');
  }

  function hideCtxMenu() {
    if (ctxMenu) ctxMenu.classList.remove('visible');
    _menuEntity = null;
  }

  // ── Public API ─────────────────────────────────────────────────────────
  window.openStratChat = function (entityCtx) { openPanel(entityCtx); };
  window.sgChat = {
    open:     openPanel,
    close:    closePanel,
    showMenu: showCtxMenu,
    hideMenu: hideCtxMenu,
  };

  // ── Boot ───────────────────────────────────────────────────────────────
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _init);
  } else {
    _init();
  }
})();

/* The parent is trusted; the book iframe cannot execute scripts. */
(function () {
  'use strict';
  const frame = document.getElementById('reading-frame');
  if (!frame) return;
  const message = document.getElementById('reading-message');
  const csrf = document.querySelector('[name=csrfmiddlewaretoken]').value;
  let position = 0, saved = null, timer = null, ready = false, initialized = false, saving = false;
  const state = fetch(frame.dataset.stateUrl, {credentials: 'same-origin', cache: 'no-store'})
    .then(r => { if (!r.ok) throw new Error('load'); return r.json(); });
  function save() {
    if (!ready || saving || position === saved) return;
    saving = true;
    const value = position;
    fetch(frame.dataset.stateUrl, {
      method: 'POST', credentials: 'same-origin', keepalive: true,
      headers: {'X-CSRFToken': csrf, 'X-Requested-With': 'XMLHttpRequest'},
      body: new URLSearchParams({position: String(value)})
    }).then(r => {
      if (!r.ok) throw new Error('save');
      saved = value;
      message.textContent = 'Сохранено: ' + Math.round(value / 100) + '%';
    }).catch(() => { message.textContent = 'Не удалось сохранить позицию. Повторите прокрутку.'; })
      .finally(() => { saving = false; if (saved === value && position !== value) save(); });
  }
  function loaded() {
    if (initialized) return;
    initialized = true;
    state.then(data => {
      const doc = frame.contentDocument;
      if (!doc || !doc.scrollingElement) throw new Error('document');
      const scroll = doc.scrollingElement;
      const range = Math.max(0, scroll.scrollHeight - scroll.clientHeight);
      position = data.position; saved = data.position;
      scroll.scrollTop = range * position / 10000;
      message.textContent = 'Позиция: ' + Math.round(position / 100) + '%';
      // Initial restoration must not overwrite a saved position on load.
      setTimeout(() => {
        ready = true;
        frame.contentWindow.addEventListener('scroll', () => {
          const max = scroll.scrollHeight - scroll.clientHeight;
          position = max > 0 ? Math.max(0, Math.min(10000, Math.round(scroll.scrollTop / max * 10000))) : 0;
          clearTimeout(timer); timer = setTimeout(save, 800);
        }, {passive: true});
      }, 100);
    }).catch(() => { message.textContent = 'Не удалось открыть книгу или загрузить позицию.'; });
  }
  frame.addEventListener('load', loaded, {once: true});
  if (frame.contentDocument && frame.contentDocument.readyState === 'complete' && frame.contentDocument.URL !== 'about:blank') loaded();
  document.addEventListener('visibilitychange', () => { if (document.hidden) save(); });
  window.addEventListener('pagehide', save);
}());

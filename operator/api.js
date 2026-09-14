/*
 * operator/api.js — thin client for /api/operator/* endpoints (iPad POS).
 *
 * Same pattern as owner/api.js: all endpoints require Telegram initData
 * (Authorization: tma <initData>), validated server-side against the
 * OPERATOR bot token. URL resolution:
 *   ?api=<url>  →  localStorage.ambar_api_url  →  window.AMBAR_API_URL  →  origin
 */
(function(){
  const AMBAR_API = (() => {
    try { const u = new URLSearchParams(window.location.search).get('api'); if (u) return u.replace(/\/$/, ''); } catch (_) {}
    try { const s = localStorage.getItem('ambar_api_url'); if (s) return s.replace(/\/$/, ''); } catch (_) {}
    return (window.AMBAR_API_URL || window.location.origin).replace(/\/$/, '');
  })();

  function getInitData() {
    try { return (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initData) || ''; }
    catch (_) { return ''; }
  }

  async function opFetch(path, opts = {}) {
    const { method = 'GET', params, body, signal } = opts;
    const qs = params ? '?' + new URLSearchParams(params).toString() : '';
    const url = AMBAR_API + path + qs;
    const headers = { 'Authorization': 'tma ' + getInitData() };
    // Тест-режим панели: тестер, который заодно настоящий оператор, включает
    // его переключателем; сервер верит заголовку только тестерам.
    try { if (localStorage.getItem('pos_test') === '1') headers['X-Ambar-Test'] = '1'; } catch (_) {}
    const reqOpts = { method, headers, signal };
    if (body !== undefined) {
      headers['Content-Type'] = 'application/json';
      reqOpts.body = JSON.stringify(body);
    }
    const res = await fetch(url, reqOpts);
    if (!res.ok) {
      const err = new Error('operator api ' + res.status);
      err.status = res.status;
      try { err.payload = await res.json(); } catch (_) {}
      // Открыли заказ не того режима (карточка тест-заказа из бота при боевой
      // панели или наоборот) — переключаем режим один раз и перезагружаемся.
      if (err.payload && err.payload.error === 'test_mismatch' && window.TEST_ALLOWED && !window.__testFlip) {
        window.__testFlip = true;
        try { localStorage.setItem('pos_test', localStorage.getItem('pos_test') === '1' ? '0' : '1'); } catch (_) {}
        setTimeout(() => location.reload(), 150);
      }
      throw err;
    }
    return res.json();
  }

  window.opApi = { opFetch, AMBAR_API };
})();

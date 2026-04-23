/*
 * owner/api.js — thin client for /api/owner/* endpoints.
 *
 * All owner endpoints require Telegram initData (Authorization: tma <initData>).
 * This module centralizes URL resolution, auth header, and error shape so
 * index.html call sites stay boring: `await ownerApi.ownerFetch('/api/owner/ping')`.
 *
 * URL resolution (matches customer app pattern):
 *   ?api=<url>  →  localStorage.ambar_api_url  →  window.AMBAR_API_URL  →  window.location.origin
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

  async function ownerFetch(path, opts = {}) {
    const { method = 'GET', params, body, signal } = opts;
    const qs = params ? '?' + new URLSearchParams(params).toString() : '';
    const url = AMBAR_API + path + qs;
    const headers = { 'Authorization': 'tma ' + getInitData() };
    const reqOpts = { method, headers, signal };
    if (body !== undefined) {
      headers['Content-Type'] = 'application/json';
      reqOpts.body = JSON.stringify(body);
    }
    const res = await fetch(url, reqOpts);
    if (!res.ok) {
      const err = new Error('owner api ' + res.status);
      err.status = res.status;
      try { err.payload = await res.json(); } catch (_) {}
      throw err;
    }
    return res.json();
  }

  window.ownerApi = { ownerFetch, AMBAR_API };
})();

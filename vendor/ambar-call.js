/*
 * ambar-call.js — голосовой звонок между приложением водителя и панелью
 * оператора. Один файл на оба приложения: протокол у них общий, и разъехаться
 * двум копиям здесь нельзя — разъедутся, и звонок молча перестанет собираться.
 *
 * Что делает: держит сокет сигналинга, спрашивает микрофон, собирает
 * соединение и отдаёт наружу голые события. Никакой разметки — как выглядит
 * звонок, каждое приложение решает само.
 *
 * Порядок предложения важен: оффер всегда делает звонящий и только после
 * того, как трубку сняли. Если делать раньше, вторая сторона получит его
 * до того, как разрешит микрофон, и ответит соединением без звука.
 *
 * Микрофон просим строго внутри касания — «позвонить» или «принять». В вебвью
 * айфона разрешение, запрошенное вне жеста, отклоняется без вопроса, и звонок
 * умирает молча.
 */
(function () {
  'use strict';

  function AmbarCall(opts) {
    this.api = (opts.api || location.origin).replace(/\/$/, '');
    this.initData = opts.initData || '';
    this.as = opts.as || '';
    this.on = opts.on || function () {};

    this.ws = null;
    this.pc = null;
    this.stream = null;      // свой микрофон
    this.audio = null;       // куда играет собеседник
    this.call = null;        // {id, peer, dir, order}
    this.roster = [];
    this.ice = [{urls: 'stun:stun.l.google.com:19302'}];
    this.me = '';
    this.defaultTarget = '';
    this._retry = 0;
    this._closing = false;
    this._pendingIce = [];
  }

  AmbarCall.prototype._emit = function (t, d) {
    try { this.on(t, d || {}); } catch (e) { console.warn('[call]', e); }
  };

  // ── сокет ────────────────────────────────────────────────────────────────
  AmbarCall.prototype.connect = function () {
    if (this.ws && this.ws.readyState <= 1) return;
    this._closing = false;
    var url = this.api.replace(/^http/, 'ws') + '/api/call/ws';
    var self = this;
    var ws;
    try { ws = new WebSocket(url); } catch (e) { this._retryLater(); return; }
    this.ws = ws;

    ws.onopen = function () {
      ws.send(JSON.stringify({t: 'auth', tma: self.initData, as: self.as}));
    };
    ws.onmessage = function (ev) {
      var m;
      try { m = JSON.parse(ev.data); } catch (e) { return; }
      self._onMessage(m);
    };
    ws.onclose = function () {
      self.ws = null;
      if (self.call) self._teardown('link');
      self._emit('offline');
      if (!self._closing) self._retryLater();
    };
    ws.onerror = function () { try { ws.close(); } catch (e) {} };
  };

  AmbarCall.prototype._retryLater = function () {
    // Растущая пауза: смена длинная, а сеть у водителя пропадает в лифте и в
    // подземном паркинге по десять раз за вечер.
    var wait = Math.min(30000, 1000 * Math.pow(2, this._retry++));
    var self = this;
    setTimeout(function () { self.connect(); }, wait);
  };

  AmbarCall.prototype.close = function () {
    this._closing = true;
    this.hangup();
    if (this.ws) { try { this.ws.close(); } catch (e) {} }
  };

  AmbarCall.prototype._send = function (m) {
    if (this.ws && this.ws.readyState === 1) this.ws.send(JSON.stringify(m));
  };

  AmbarCall.prototype._onMessage = function (m) {
    switch (m.t) {
      case 'ready':
        this._retry = 0;
        this.me = m.me;
        this.roster = m.roster || [];
        this.defaultTarget = m.peer || '';
        if (m.ice && m.ice.length) this.ice = m.ice;
        this._emit('ready', m);
        break;

      case 'roster':                                // кто-то вошёл или вышел
        this.roster = m.roster || [];
        this._emit('roster', {roster: this.roster});
        break;

      case 'denied':
        this._emit('denied');
        break;

      case 'ring':                                  // нам звонят
        this.call = {id: m.call, peer: m.frm, dir: 'in', order: m.order || '',
                     instead_of: m.instead_of || ''};
        this._emit('ring', this.call);
        break;

      case 'calling':                               // звоним мы
        this.call = {id: m.call, peer: m.to, dir: 'out', order: '',
                     instead_of: m.instead_of || '', note: m.note || ''};
        this._emit('calling', this.call);
        break;

      case 'accepted':                              // нашу трубку сняли
        if (!this.call || this.call.id !== m.call) break;
        this.call.peer = m.by || this.call.peer;
        this._emit('talking', this.call);
        this._startMedia(true);
        break;

      case 'joined':                                // мы сняли трубку
        if (!this.call || this.call.id !== m.call) break;
        this._emit('talking', this.call);
        this._startMedia(false);
        break;

      case 'cancel':                                // взяли на другом устройстве
        if (this.call && this.call.id === m.call) this._teardown('cancel');
        break;

      case 'failed':
        this.call = null;
        this._emit('failed', {why: m.why});
        break;

      case 'end':
        if (this.call && this.call.id === m.call) this._teardown(m.why || 'end');
        break;

      case 'sdp':
        this._onSdp(m.data);
        break;

      case 'ice':
        this._onIce(m.data);
        break;
    }
  };

  // ── действия ─────────────────────────────────────────────────────────────
  // Микрофон просим ЗДЕСЬ, внутри касания, а не когда трубку снимут: в вебвью
  // айфона запрос вне жеста отклоняется молча.
  AmbarCall.prototype.dial = function (toKey, order) {
    var self = this;
    return this._mic().then(function () {
      self._send({t: 'call', to: toKey || self.defaultTarget || '', order: order || ''});
    });
  };

  AmbarCall.prototype.accept = function () {
    if (!this.call) return Promise.resolve();
    var self = this, id = this.call.id;
    return this._mic().then(function () { self._send({t: 'accept', call: id}); });
  };

  AmbarCall.prototype.reject = function () {
    if (!this.call) return;
    this._send({t: 'reject', call: this.call.id});
    this._teardown('rejected');
  };

  AmbarCall.prototype.hangup = function () {
    if (!this.call) return;
    this._send({t: 'bye', call: this.call.id});
    this._teardown('hangup');
  };

  AmbarCall.prototype.mute = function (on) {
    if (!this.stream) return false;
    this.stream.getAudioTracks().forEach(function (t) { t.enabled = !on; });
    return !!on;
  };

  // ── микрофон и соединение ────────────────────────────────────────────────
  AmbarCall.prototype._mic = function () {
    if (this.stream) return Promise.resolve(this.stream);
    var self = this;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      this._emit('nomic', {why: 'unsupported'});
      return Promise.reject(new Error('no getUserMedia'));
    }
    return navigator.mediaDevices.getUserMedia({
      audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true}
    }).then(function (s) {
      self.stream = s;
      // Элемент воспроизведения создаём тоже в жесте: айфон разрешает звук
      // только у того элемента, чей play() случился по касанию.
      if (!self.audio) {
        var a = document.createElement('audio');
        a.autoplay = true; a.playsInline = true;
        a.setAttribute('playsinline', '');
        a.style.display = 'none';
        document.body.appendChild(a);
        self.audio = a;
      }
      try { self.audio.play().catch(function () {}); } catch (e) {}
      return s;
    }).catch(function (err) {
      self._emit('nomic', {why: err && err.name || 'error'});
      throw err;
    });
  };

  AmbarCall.prototype._startMedia = function (isCaller) {
    var self = this;
    var pc = new RTCPeerConnection({iceServers: this.ice});
    this.pc = pc;
    this._pendingIce = [];

    this.stream.getTracks().forEach(function (t) { pc.addTrack(t, self.stream); });

    pc.ontrack = function (e) {
      if (self.audio && e.streams && e.streams[0]) {
        self.audio.srcObject = e.streams[0];
        try { self.audio.play().catch(function () {}); } catch (err) {}
      }
    };
    pc.onicecandidate = function (e) {
      if (e.candidate) self._send({t: 'ice', data: e.candidate});
    };
    pc.onconnectionstatechange = function () {
      self._emit('net', {state: pc.connectionState});
      if (pc.connectionState === 'failed') self._teardown('net');
    };

    if (isCaller) {
      pc.createOffer().then(function (o) {
        return pc.setLocalDescription(o);
      }).then(function () {
        self._send({t: 'sdp', data: pc.localDescription});
      }).catch(function (e) { console.warn('[call] offer', e); });
    }
  };

  AmbarCall.prototype._onSdp = function (sdp) {
    if (!this.pc || !sdp) return;
    var self = this;
    this.pc.setRemoteDescription(sdp).then(function () {
      // Кандидаты умеют обгонять описание — те, что пришли раньше, ждали здесь.
      self._pendingIce.forEach(function (c) {
        self.pc.addIceCandidate(c).catch(function () {});
      });
      self._pendingIce = [];
      if (sdp.type === 'offer') {
        return self.pc.createAnswer().then(function (a) {
          return self.pc.setLocalDescription(a);
        }).then(function () {
          self._send({t: 'sdp', data: self.pc.localDescription});
        });
      }
    }).catch(function (e) { console.warn('[call] sdp', e); });
  };

  AmbarCall.prototype._onIce = function (c) {
    if (!this.pc || !c) return;
    if (!this.pc.remoteDescription || !this.pc.remoteDescription.type) {
      this._pendingIce.push(c);
      return;
    }
    this.pc.addIceCandidate(c).catch(function () {});
  };

  AmbarCall.prototype._teardown = function (why) {
    if (this.pc) { try { this.pc.close(); } catch (e) {} this.pc = null; }
    if (this.stream) {
      this.stream.getTracks().forEach(function (t) { try { t.stop(); } catch (e) {} });
      this.stream = null;
    }
    if (this.audio) { try { this.audio.srcObject = null; } catch (e) {} }
    this.call = null;
    this._pendingIce = [];
    this._emit('ended', {why: why});
  };

  window.AmbarCall = AmbarCall;
})();

/*
 * ambar-call.js — голосовой звонок между приложением водителя и панелью
 * оператора. Один файл на оба приложения: протокол у них общий, и разъехаться
 * двум копиям здесь нельзя — разъедутся, и звонок молча перестанет собираться.
 *
 * Что делает: держит сокет сигналинга, микрофон, соединение, тоны и качество
 * связи, а наружу отдаёт голые события. Никакой разметки — как выглядит
 * звонок, каждое приложение решает само.
 *
 * Микрофон — один раз за сеанс
 * ----------------------------
 * Раньше дорожка останавливалась после каждого разговора, и следующий звонок
 * снова упирался в системное окно. Теперь микрофон берётся однажды и живёт
 * до конца сеанса; между звонками дорожка ВЫКЛЮЧАЕТСЯ (enabled = false), а не
 * останавливается — окна больше нет, и записи тоже нет.
 *
 * Отпускаем его только когда приложение свернули дольше чем на полминуты:
 * иначе телефон всю смену показывал бы, что микрофон занят, и это правильно
 * пугало бы человека.
 *
 * Просить приходится из касания — «позвонить», «принять» или отдельная кнопка
 * на экране связи. В вебвью айфона запрос вне жеста отклоняется молча.
 * Программного способа спросить «от имени бота» не существует: в API мини-аппов
 * есть геопозиция, биометрия, хранилище — микрофона нет, окно рисует система и
 * называет в нём адрес приложения. Поэтому важно, чтобы окно всплывало один раз
 * и в понятный момент, а не посреди входящего звонка.
 *
 * Видео
 * -----
 * Та же дорожка в том же соединении — отдельного канала не нужно. Камера
 * решается в момент вызова: голосовой звонок остаётся голосовым, видео просят
 * явно. Внутри разговора камеру можно выключить и переключить на заднюю, и
 * то и другое без пересборки соединения — меняется только дорожка.
 *
 * Битрейт видео ограничен намеренно: водитель на 4G, и картинка, которая ест
 * весь канал, забирает его у голоса. Голос важнее картинки всегда.
 *
 * Порядок предложения важен: оффер всегда делает звонящий и только после того,
 * как трубку сняли. Если делать раньше, вторая сторона получит его до того, как
 * разрешит микрофон, и ответит соединением без звука.
 */
(function () {
  'use strict';

  // Свернули дольше — отпускаем микрофон. Полминуты оказалось мало: AMBAR STAR
  // сворачивают и разворачивают десятки раз за вечер, и каждый раз система
  // спрашивала разрешение заново. Три минуты закрывают обычное переключение
  // между приложениями; за это время индикатор записи горит, но дорожка
  // выключена и в неё ничего не пишется.
  var MIC_FREE_AFTER = 180000;
  var STATS_EVERY = 3000;        // как часто смотрим на качество связи
  var ICE_RESTART_AFTER = 4000;  // столько ждём, прежде чем пересобирать связь
  var VIDEO_MAX_KBPS = 600;      // потолок картинки: голос важнее её всегда

  // ── тоны ─────────────────────────────────────────────────────────────────
  // Гудки звонящему и звонок принимающему. Человек должен слышать, что связь
  // идёт: молчащий экран «Звоним…» неотличим от сломанного.
  function Tones() {
    this.ac = null;
    this.timer = null;
    this.kind = '';
  }
  Tones.prototype._ctx = function () {
    try {
      if (!this.ac) this.ac = new (window.AudioContext || window.webkitAudioContext)();
      if (this.ac.state === 'suspended') this.ac.resume();
    } catch (e) { this.ac = null; }
    return this.ac;
  };
  Tones.prototype._beep = function (freq, dur, gain) {
    var ac = this._ctx();
    if (!ac || ac.state !== 'running') return;
    var o = ac.createOscillator(), g = ac.createGain();
    o.type = 'sine';
    o.frequency.value = freq;
    g.gain.setValueAtTime(0, ac.currentTime);
    g.gain.linearRampToValueAtTime(gain == null ? 0.16 : gain, ac.currentTime + 0.03);
    g.gain.linearRampToValueAtTime(0, ac.currentTime + dur);
    o.connect(g); g.connect(ac.destination);
    o.start(); o.stop(ac.currentTime + dur + 0.02);
  };
  // Гудок вызова: длинный тон раз в четыре секунды, как в телефоне.
  Tones.prototype.ringback = function () { this._loop('ringback', 4000, function (t) {
    t._beep(425, 1.0, 0.10); }); };
  // Звонок: две короткие трели, чтобы отличать от гудка и от нового заказа.
  Tones.prototype.ring = function () { this._loop('ring', 2200, function (t) {
    t._beep(620, 0.2, 0.22); setTimeout(function () { t._beep(620, 0.2, 0.22); }, 260); }); };
  // Занято и обрыв — по одному разу, зацикливать нечего.
  Tones.prototype.busy = function () { this.stop(); this._beep(480, 0.25, 0.16);
    var t = this; setTimeout(function () { t._beep(480, 0.25, 0.16); }, 400); };
  Tones.prototype.bye = function () { this.stop(); this._beep(330, 0.28, 0.13); };
  Tones.prototype._loop = function (kind, every, fn) {
    if (this.kind === kind) return;
    this.stop();
    this.kind = kind;
    var t = this;
    fn(t);
    this.timer = setInterval(function () { fn(t); }, every);
  };
  Tones.prototype.stop = function () {
    if (this.timer) { clearInterval(this.timer); this.timer = null; }
    this.kind = '';
  };

  // ── клиент ───────────────────────────────────────────────────────────────
  function AmbarCall(opts) {
    this.api = (opts.api || location.origin).replace(/\/$/, '');
    this.initData = opts.initData || '';
    this.as = opts.as || '';
    this.on = opts.on || function () {};

    this.ws = null;
    this.pc = null;
    this.stream = null;      // микрофон, живёт между звонками
    this.cam = null;         // камера — только на время видеозвонка
    this.remote = null;      // что пришло от собеседника
    this.facing = 'user';    // какая камера включена
    this.video = false;      // это видеозвонок
    this.audio = null;       // куда играет собеседник
    this.call = null;        // {id, peer, dir, order, note, say}
    this.roster = [];
    this.recent = [];        // последние звонки — приходят с сервера
    this.ice = [{urls: 'stun:stun.l.google.com:19302'}];
    this.me = '';
    this.kind = '';
    this.defaultTarget = '';
    this.quality = '';       // '', 'good', 'weak', 'bad'
    this.tones = new Tones();

    this._retry = 0;
    this._closing = false;
    this._pendingIce = [];
    this._statsT = null;
    this._lastStats = null;
    this._iceRestartT = null;
    this._hideT = null;
    this._wake = null;

    var self = this;
    // Свернули приложение — отпускаем микрофон, но не сразу: короткое
    // переключение на карты не должно стоить нового окна с разрешением.
    document.addEventListener('visibilitychange', function () {
      clearTimeout(self._hideT);
      if (document.hidden) {
        self._hideT = setTimeout(function () {
          if (!self.call) self._freeMic();
        }, MIC_FREE_AFTER);
      }
    });
  }

  AmbarCall.prototype._emit = function (t, d) {
    try { this.on(t, d || {}); } catch (e) { console.warn('[call]', e); }
  };

  // Разрешение уже давали — приложение может не показывать объяснение снова.
  AmbarCall.prototype.micGranted = function () {
    try { return localStorage.getItem('ambar_mic_ok') === '1'; } catch (e) { return false; }
  };
  AmbarCall.prototype.micLive = function () {
    return !!(this.stream && this.stream.getAudioTracks().some(
      function (t) { return t.readyState === 'live'; }));
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
    // Растущая пауза: смена длинная, а связь у водителя пропадает в лифте и в
    // подземном паркинге по десять раз за вечер.
    var wait = Math.min(30000, 1000 * Math.pow(2, this._retry++));
    var self = this;
    setTimeout(function () { self.connect(); }, wait);
  };

  AmbarCall.prototype.close = function () {
    this._closing = true;
    this.hangup();
    this._freeMic();
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
        this.kind = m.kind || '';
        this.roster = m.roster || [];
        this.recent = m.recent || [];
        this.defaultTarget = m.peer || '';
        if (m.ice && m.ice.length) this.ice = m.ice;
        this._emit('ready', m);
        break;

      case 'roster':
        this.roster = m.roster || [];
        this._emit('roster', {roster: this.roster});
        break;

      case 'recent':
        this.recent = m.recent || [];
        this._emit('recent', {recent: this.recent});
        break;

      case 'denied':
        this._emit('denied');
        break;

      case 'ring':                                  // нам звонят
        this.call = {id: m.call, peer: m.frm, dir: 'in', order: m.order || '',
                     kind: m.kind || '', video: !!m.video};
        this.tones.ring();
        this._emit('ring', this.call);
        break;

      case 'calling':                               // звоним мы
        this.call = {id: m.call, peer: m.to, dir: 'out', order: '', note: m.note || '',
                     video: this.video};
        this.tones.ringback();
        this._emit('calling', this.call);
        break;

      case 'accepted':                              // нашу трубку сняли
        if (!this.call || this.call.id !== m.call) break;
        this.tones.stop();
        this.call.peer = m.by || this.call.peer;
        this._emit('talking', this.call);
        this._startMedia(true);
        break;

      case 'joined':                                // мы сняли трубку
        if (!this.call || this.call.id !== m.call) break;
        this.tones.stop();
        this._emit('talking', this.call);
        this._startMedia(false);
        break;

      case 'cancel':                                // взяли на другом устройстве
        if (this.call && this.call.id === m.call) this._teardown('cancel');
        break;

      case 'failed':
        this.call = null;
        this.tones.stop();
        if (m.why === 'busy_them') this.tones.busy();
        this._emit('failed', {why: m.why, peer: m.peer || ''});
        break;

      case 'end':
        if (this.call && this.call.id === m.call) {
          if (m.say) this.call.say = m.say;
          this._teardown(m.why || 'end');
        }
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
  AmbarCall.prototype.dial = function (toKey, order, video) {
    var self = this;
    this.video = !!video;
    return this._mic().then(function () {
      return self.video ? self._camOn() : null;
    }).then(function () {
      self._send({t: 'call', to: toKey || self.defaultTarget || '',
                  order: order || '', video: self.video});
    });
  };

  AmbarCall.prototype.accept = function () {
    if (!this.call) return Promise.resolve();
    var self = this, id = this.call.id;
    this.video = !!this.call.video;
    return this._mic().then(function () {
      return self.video ? self._camOn() : null;
    }).then(function () { self._send({t: 'accept', call: id}); })
      .catch(function (e) {
        // Камеры может не быть или её могут не дать — звонок от этого умирать
        // не должен: продолжаем голосом.
        if (self.video) { self.video = false; self._emit('novideo', {}); }
        self._send({t: 'accept', call: id});
      });
  };

  // Отклонить можно молча или словом. Слово короткое и заранее заготовленное:
  // за рулём набирать нечего, а «занят» и «перезвоню» закрывают почти всё.
  AmbarCall.prototype.reject = function (say) {
    if (!this.call) return;
    this._send({t: 'reject', call: this.call.id, say: say || ''});
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

  AmbarCall.prototype.muted = function () {
    return !!(this.stream && this.stream.getAudioTracks().some(
      function (t) { return !t.enabled; }));
  };

  // ── микрофон ─────────────────────────────────────────────────────────────
  // Отдельная кнопка «разрешить» на экране связи зовёт сюда же: разрешение
  // спрашивается в спокойный момент, а не когда телефон уже звонит.
  AmbarCall.prototype.warmMic = function () { return this._mic(); };

  AmbarCall.prototype._mic = function () {
    var self = this;
    if (this.micLive()) {                      // уже есть — просто включаем
      this.stream.getAudioTracks().forEach(function (t) { t.enabled = true; });
      return Promise.resolve(this.stream);
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      this._emit('nomic', {why: 'unsupported'});
      return Promise.reject(new Error('no getUserMedia'));
    }
    return navigator.mediaDevices.getUserMedia({
      audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true}
    }).then(function (s) {
      self.stream = s;
      try { localStorage.setItem('ambar_mic_ok', '1'); } catch (e) {}
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
      self._emit('mic', {ok: true});
      return s;
    }).catch(function (err) {
      var why = (err && err.name) || 'error';
      if (why === 'NotAllowedError') { try { localStorage.removeItem('ambar_mic_ok'); } catch (e) {} }
      self._emit('nomic', {why: why});
      throw err;
    });
  };

  // Камера живёт только на время разговора — в отличие от микрофона её держать
  // между звонками нельзя: горящий глазок весь день пугает не зря.
  AmbarCall.prototype._camOn = function (facing) {
    var self = this;
    this.facing = facing || this.facing || 'user';
    return navigator.mediaDevices.getUserMedia({
      video: {facingMode: this.facing, width: {ideal: 640}, height: {ideal: 480},
              frameRate: {ideal: 20, max: 24}}
    }).then(function (s) {
      var track = s.getVideoTracks()[0];
      if (!track) throw new Error('no camera');
      self.cam = s;
      self.video = true;
      // Соединение уже собрано — просто подменяем дорожку, без пересборки.
      var sender = self.pc && self.pc.getSenders().find(function (x) {
        return x.track && x.track.kind === 'video';
      });
      if (sender) sender.replaceTrack(track);
      else if (self.pc) { self.pc.addTrack(track, self.stream || s); self._capVideo(); }
      self._emit('cam', {on: true, facing: self.facing, stream: s});
      return s;
    });
  };

  AmbarCall.prototype._camOff = function () {
    if (!this.cam) return;
    this.cam.getTracks().forEach(function (t) { try { t.stop(); } catch (e) {} });
    this.cam = null;
    var sender = this.pc && this.pc.getSenders().find(function (x) {
      return x.track && x.track.kind === 'video';
    });
    if (sender) { try { sender.replaceTrack(null); } catch (e) {} }
    this._emit('cam', {on: false});
  };

  // Выключить и включить свою камеру посреди разговора.
  AmbarCall.prototype.camera = function (on) {
    if (on) return this._camOn().catch(function () {});
    this._camOff();
    return Promise.resolve();
  };

  // Переднюю на заднюю и обратно: показать полку или подъезд удобнее задней.
  AmbarCall.prototype.flip = function () {
    var next = this.facing === 'user' ? 'environment' : 'user';
    var old = this.cam;
    var self = this;
    return this._camOn(next).then(function (s) {
      if (old && old !== s) old.getTracks().forEach(function (t) { try { t.stop(); } catch (e) {} });
      return s;
    }).catch(function () { self.facing = self.facing === 'user' ? 'environment' : 'user'; });
  };

  // Потолок битрейта картинки. Без него видео на 4G съедает канал и первым
  // страдает голос — а голос в работе важнее.
  AmbarCall.prototype._capVideo = function () {
    var pc = this.pc;
    if (!pc) return;
    pc.getSenders().forEach(function (s) {
      if (!s.track || s.track.kind !== 'video' || !s.getParameters) return;
      try {
        var p = s.getParameters();
        p.encodings = p.encodings && p.encodings.length ? p.encodings : [{}];
        p.encodings[0].maxBitrate = VIDEO_MAX_KBPS * 1000;
        p.encodings[0].maxFramerate = 24;
        s.setParameters(p);
      } catch (e) {}
    });
  };

  AmbarCall.prototype._freeMic = function () {
    if (!this.stream) return;
    this.stream.getTracks().forEach(function (t) { try { t.stop(); } catch (e) {} });
    this.stream = null;
    this._emit('mic', {ok: false});
  };

  // ── соединение ───────────────────────────────────────────────────────────
  AmbarCall.prototype._startMedia = function (isCaller) {
    var self = this;
    var pc = new RTCPeerConnection({iceServers: this.ice});
    this.pc = pc;
    this._pendingIce = [];
    this._isCaller = isCaller;

    this.stream.getAudioTracks().forEach(function (t) {
      t.enabled = true;
      pc.addTrack(t, self.stream);
    });
    if (this.cam) this.cam.getVideoTracks().forEach(function (t) { pc.addTrack(t, self.cam); });

    pc.ontrack = function (e) {
      var st = e.streams && e.streams[0];
      if (!st) return;
      self.remote = st;
      // Звук всегда в свой элемент: видео может быть выключено, а слышать надо.
      if (self.audio && e.track.kind === 'audio') {
        self.audio.srcObject = st;
        try { self.audio.play().catch(function () {}); } catch (err) {}
      }
      if (e.track.kind === 'video') self.video = true;
      self._emit('stream', {stream: st, kind: e.track.kind});
    };
    pc.onicecandidate = function (e) {
      if (e.candidate) self._send({t: 'ice', data: e.candidate});
    };
    pc.onconnectionstatechange = function () {
      var st = pc.connectionState;
      self._emit('net', {state: st});
      if (st === 'connected') {
        clearTimeout(self._iceRestartT);
        self._quality('good');
      }
      // Разрыв в тоннеле или в лифте — не повод класть трубку. Даём связи
      // собраться заново и только потом сдаёмся.
      if (st === 'disconnected') {
        self._quality('bad');
        clearTimeout(self._iceRestartT);
        self._iceRestartT = setTimeout(function () { self._restartIce(); }, ICE_RESTART_AFTER);
      }
      if (st === 'failed') {
        self._quality('bad');
        self._restartIce();
      }
    };

    this._watchQuality();
    this._keepAwake(true);

    this._capVideo();

    if (isCaller) {
      pc.createOffer().then(function (o) { return pc.setLocalDescription(o); })
        .then(function () { self._send({t: 'sdp', data: pc.localDescription}); })
        .catch(function (e) { console.warn('[call] offer', e); });
    }
  };

  // Пересборка связи без потери разговора: та же сессия, новые кандидаты.
  AmbarCall.prototype._restartIce = function () {
    var pc = this.pc, self = this;
    if (!pc || !this._isCaller || pc.connectionState === 'connected') return;
    try {
      pc.createOffer({iceRestart: true})
        .then(function (o) { return pc.setLocalDescription(o); })
        .then(function () { self._send({t: 'sdp', data: pc.localDescription}); })
        .catch(function () {});
    } catch (e) {}
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

  // ── качество связи ───────────────────────────────────────────────────────
  // Водитель на 4G в подземном паркинге должен видеть, что связь плохая, а не
  // думать, что собеседник замолчал.
  AmbarCall.prototype._watchQuality = function () {
    var self = this;
    clearInterval(this._statsT);
    this._lastStats = null;
    this._statsT = setInterval(function () {
      if (!self.pc) return;
      self.pc.getStats().then(function (rep) {
        var inb = null;
        rep.forEach(function (r) { if (r.type === 'inbound-rtp' && r.kind === 'audio') inb = r; });
        if (!inb) return;
        var prev = self._lastStats;
        self._lastStats = {lost: inb.packetsLost || 0, got: inb.packetsReceived || 0,
                           jitter: inb.jitter || 0};
        if (!prev) return;
        var dLost = self._lastStats.lost - prev.lost;
        var dGot = self._lastStats.got - prev.got;
        if (dGot <= 0) { self._quality('bad'); return; }   // тишина в трубке
        var loss = dLost / (dLost + dGot);
        var jit = self._lastStats.jitter;
        self._quality(loss > 0.12 || jit > 0.12 ? 'bad'
                    : loss > 0.03 || jit > 0.05 ? 'weak' : 'good');
      }).catch(function () {});
    }, STATS_EVERY);
  };

  AmbarCall.prototype._quality = function (q) {
    if (this.quality === q) return;
    this.quality = q;
    this._emit('quality', {quality: q});
  };

  // Экран не должен гаснуть посреди разговора: телефон в руке у уха, а
  // погасший экран в вебвью означает остановленный звонок.
  AmbarCall.prototype._keepAwake = function (on) {
    var self = this;
    if (on) {
      try {
        if (navigator.wakeLock && !this._wake) {
          navigator.wakeLock.request('screen').then(function (w) { self._wake = w; })
            .catch(function () {});
        }
      } catch (e) {}
    } else if (this._wake) {
      try { this._wake.release(); } catch (e) {}
      this._wake = null;
    }
  };

  AmbarCall.prototype._teardown = function (why) {
    this.tones.stop();
    if (why === 'hangup' || why === 'end') this.tones.bye();
    clearInterval(this._statsT); this._statsT = null;
    clearTimeout(this._iceRestartT);
    this._keepAwake(false);
    if (this.pc) { try { this.pc.close(); } catch (e) {} this.pc = null; }
    // Микрофон НЕ останавливаем — только глушим. Следующий звонок не должен
    // снова упираться в системное окно.
    if (this.stream) {
      this.stream.getAudioTracks().forEach(function (t) { t.enabled = false; });
    }
    if (this.audio) { try { this.audio.srcObject = null; } catch (e) {} }
    // Камеру гасим всегда: в отличие от микрофона её нельзя оставлять включённой
    // между звонками — горящий глазок пугает и правильно делает.
    this._camOff();
    this.remote = null;
    this.video = false;
    var say = this.call && this.call.say;
    this.call = null;
    this._pendingIce = [];
    this.quality = '';
    this._emit('ended', {why: why, say: say || ''});
  };

  window.AmbarCall = AmbarCall;
})();

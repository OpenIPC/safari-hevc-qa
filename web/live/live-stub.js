// Stub the camera so the REAL Live-page scripts run unchanged in the harness.
// Defines the handful of main.js globals the Live scripts expect (verbatim from
// main.js), points config at the captured config.json, serves an empty sources
// list, forces the MSE transport the reporter uses, and replays the recorded
// HEVC over a fake /ws/video WebSocket. Loaded BEFORE the Live scripts.
(function () {
  'use strict';

  // Capture any error on the page so a headless run can report why it stalled.
  window.__err = [];
  window.addEventListener('error', function (e) {
    try { window.__err.push(String((e && e.message) || e) + ' @ ' + String((e && e.filename) || '') + ':' + ((e && e.lineno) || '')); } catch (x) {}
  });
  window.addEventListener('unhandledrejection', function (e) {
    try { window.__err.push('reject: ' + String((e && e.reason && e.reason.message) || (e && e.reason) || e)); } catch (x) {}
  });

  // --- main.js helpers (verbatim) ---
  window.$ = function (n) { return document.querySelector(n); };
  window.$$ = function (n) { return Array.prototype.slice.call(document.querySelectorAll(n)); };
  window.mjGet = function (cfg, dot) {
    return dot.split('.').reduce(function (o, k) { return o == null ? undefined : o[k]; }, cfg);
  };
  window.apiFetch = function (url, init) { return fetch(url, init); };

  var _cfg;
  window.mjConfig = function () {
    if (!_cfg) {
      var sb = new URLSearchParams(location.search).get('stream') || 'stream';
      var cfgFile = sb === 'stream' ? 'config.json' : 'config-' + sb.replace('stream-', '') + '.json';
      _cfg = fetch(cfgFile).then(function (r) { return r.json(); }).catch(function () { return {}; });
    }
    return _cfg;
  };
  window.mjSources = function () { return Promise.resolve([]); };   // one on-board source

  // Reporter's case: WebRTC present, MSE picked.
  try { localStorage.setItem('mj-transport-pick', 'mse'); } catch (e) {}

  // Initial stage geometry — ?stage=WxH, so a portrait phone (tall/narrow, where
  // a 4:3 Fill picture is height-driven and a URL-bar height change reflows it)
  // can be reproduced. Set before the Live scripts lay out.
  (function () {
    var s = new URLSearchParams(location.search).get('stage');
    if (!s) return;
    var wh = s.split('x'), st = document.getElementById('mj-stage');
    if (st && wh.length === 2) { st.style.width = (+wh[0]) + 'px'; st.style.height = (+wh[1]) + 'px'; }
  })();

  // --- fake /ws/video: replay the recorded HEVC (init + one fragment/frame) ---
  // ?stream=NAME picks web/NAME.json / web/NAME.bin (default the 16:9-ish clip).
  var streamBase = new URLSearchParams(location.search).get('stream') || 'stream';
  var manP = fetch('../' + streamBase + '.json').then(function (r) { return r.json(); });
  var binP = fetch('../' + streamBase + '.bin').then(function (r) { return r.arrayBuffer(); });
  var ready = Promise.all([manP, binP]).then(function (res) {
    var man = res[0], buf = res[1];
    return {
      init: buf.slice(0, man.initLength),
      frags: man.fragments.map(function (f) { return { data: buf.slice(f.offset, f.offset + f.length), arrivalMs: f.arrivalMs }; }),
      text: man.textInit || JSON.stringify({ type: 'init', codec: 'h265', codecString: man.codecString, mime: man.mime, width: 2592, height: 1520, stream: 0, audioCodec: null })
    };
  });
  window.__streamReady = ready;

  var RealWS = window.WebSocket;
  function FakeWS(url) {
    var self = this;
    this.url = String(url); this.readyState = 0; this.binaryType = 'blob';
    this.onopen = this.onmessage = this.onclose = this.onerror = null;
    this._closed = false; this._timers = [];
    if (this.url.indexOf('/ws/video') < 0) { return new RealWS(url); }  // anything else is real
    ready.then(function (S) {
      if (self._closed) return;
      self.readyState = 1;
      if (self.onopen) self.onopen({});
      deliver(self, S.text, 0);
      deliver(self, S.init, 0);
      S.frags.forEach(function (fr) { deliver(self, fr.data, fr.arrivalMs); });
    });
  }
  FakeWS.prototype.send = function () {};
  FakeWS.prototype.addEventListener = function (t, fn) { this['on' + t] = fn; };
  FakeWS.prototype.removeEventListener = function () {};
  FakeWS.prototype.close = function () {
    this._closed = true; this.readyState = 3;
    this._timers.forEach(clearTimeout);
    if (this.onclose) this.onclose({ code: 1000 });
  };
  function deliver(sock, payload, atMs) {
    var t = setTimeout(function () {
      if (sock._closed || sock.readyState !== 1 || !sock.onmessage) return;
      sock.onmessage({ data: payload });
    }, atMs);
    sock._timers.push(t);
  }
  for (var k in RealWS) { try { FakeWS[k] = RealWS[k]; } catch (e) {} }
  window.WebSocket = FakeWS;
})();

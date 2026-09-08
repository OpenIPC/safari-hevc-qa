// Drive the real majestic-webui preview.js (window.MajesticVideo) on Safari.
//
// preview.js opens `new WebSocket('.../ws/video?stream=N')` and consumes a text
// {type:'init',...} message then binary fMP4 (init segment, then one moof+mdat
// per frame). We stub WebSocket to replay a recording of exactly that, so the
// SHIPPED player code runs unchanged — its queue, its coalescing (the #411 fix),
// its reconnect logic — and we measure whether Safari plays it through or falls
// into the #335 flash. Verdict on window.__result for run.py.
(function () {
  'use strict';
  var q = new URLSearchParams(location.search);
  var maxMs = +(q.get('ms') || 25000);
  var base = q.get('stream') || 'stream';
  // Which player build to test: preview.js (with the #411 fix) by default, or
  // preview-unfixed.js (master, per-frame appends) for the before/after control.
  var playerSrc = (q.get('player') || 'preview.js').replace(/[^A-Za-z0-9._-]/g, '');
  if (!window.MajesticVideo) {
    var s = document.createElement('script');
    s.src = playerSrc;
    s.onload = main;
    s.onerror = function () {
      document.getElementById('log').textContent = 'failed to load ' + playerSrc;
      window.__result = { note: 'player-load-fail', reproduced: null }; window.__done = true;
      document.title = 'DONE';
    };
    document.head.appendChild(s);
  } else {
    main();
  }

  function main() {

  var R = {
    ua: navigator.userAgent, codec: null, states: [], reconnects: 0,
    playingAt: null, blackFrames: 0, blackEvents: 0, maxCurrentTime: 0,
    timeAdvances: 0, playedSeconds: 0, streamSeconds: 0, mjpegFallback: false,
    fellBackReason: null, samples: [], note: '', reproduced: null
  };
  window.__progress = R;
  window.__done = false;

  var canvas = document.getElementById('c');
  var ctx = canvas.getContext('2d', { willReadFrequently: true });
  var logEl = document.getElementById('log');
  function note(m) { R.note += m + ' | '; }
  function render() {
    logEl.textContent = JSON.stringify({
      codec: R.codec, states: R.states.slice(-6), reconnects: R.reconnects,
      t: R.maxCurrentTime.toFixed(2), blackEvents: R.blackEvents, note: R.note.slice(-240)
    }, null, 1);
  }
  function curVideo() { return document.querySelector('#stage video'); }
  function luma() {
    try {
      var v = curVideo();
      if (!v || !v.videoWidth) return null;
      canvas.width = 64; canvas.height = 36;
      ctx.drawImage(v, 0, 0, 64, 36);
      var d = ctx.getImageData(0, 0, 64, 36).data, s = 0;
      for (var i = 0; i < d.length; i += 4) s += 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
      return s / (d.length / 4);
    } catch (e) { return null; }
  }

  Promise.all([
    fetch(base + '.json').then(function (r) { return r.json(); }),
    fetch(base + '.bin').then(function (r) { return r.arrayBuffer(); })
  ]).then(function (res) {
    var man = res[0], buf = res[1];
    R.codec = man.codecString;
    R.streamSeconds = man.fragments.length ? man.fragments[man.fragments.length - 1].arrivalMs / 1000 : 0;
    var initSeg = buf.slice(0, man.initLength);
    var frags = man.fragments.map(function (f) {
      return { data: buf.slice(f.offset, f.offset + f.length), arrivalMs: f.arrivalMs };
    });
    var textInit = man.textInit || JSON.stringify({
      type: 'init', codec: 'h265', codecString: man.codecString, mime: man.mime,
      width: 2592, height: 1520, stream: 0, audioCodec: null
    });

    // The stubbed WebSocket: one instance per preview.js open()/reconnect().
    function FakeWS(url) {
      var self = this;
      this.url = url; this.readyState = 0; this.binaryType = 'blob';
      this.onopen = this.onmessage = this.onclose = this.onerror = null;
      this._closed = false; this._timers = [];
      setTimeout(function () {
        if (self._closed) return;
        self.readyState = 1;
        if (self.onopen) self.onopen({});
        // text init, then binary init segment, then fragments at their cadence.
        deliver(self, textInit, 0);
        deliver(self, initSeg, 0);
        frags.forEach(function (fr) { deliver(self, fr.data, fr.arrivalMs); });
        // Leave the socket open after the last fragment; don't trigger a
        // reconnect. The run ends on the maxMs timer.
      }, 5);
    }
    FakeWS.prototype.send = function () {};
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
    window.WebSocket = FakeWS;

    // Instrument the video element preview.js manages (it clones it on connect).
    var lastBlack = false;
    var sampler = setInterval(function () {
      var v = curVideo();
      if (v) {
        var t = v.currentTime;
        if (t > R.maxCurrentTime + 0.001) { R.timeAdvances++; R.maxCurrentTime = t; }
      }
      var b = luma();
      if (b !== null) {
        var isBlack = b < 4;
        if (isBlack) R.blackFrames++;
        if (isBlack && !lastBlack) R.blackEvents++;
        lastBlack = isBlack;
        if (R.samples.length < 700) R.samples.push({ t: +R.maxCurrentTime.toFixed(2), luma: +b.toFixed(1) });
      }
      render();
    }, 33);

    // Attach the real player. onState('playing') is the picture; a fall to
    // 'mjpeg'/'nosignal' or repeated 'connecting' is the fault surfacing.
    var player = window.MajesticVideo.attach(document.getElementById('v'), {
      stream: 0,
      onState: function (state, detail) {
        R.states.push(state + (detail ? ':' + detail : ''));
        if (state === 'playing' && R.playingAt === null) R.playingAt = R.maxCurrentTime;
        if (state === 'connecting') R.reconnects++;
        if (state === 'mjpeg') { R.mjpegFallback = true; R.fellBackReason = detail || ''; }
        note('state=' + state + (detail ? '(' + detail + ')' : ''));
      },
      onCodec: function (c) { note('codec=' + c); }
    });

    // A real user gesture for autoplay under WebDriver.
    window.__play = function () { var v = curVideo(); if (v) { v.muted = true; var p = v.play && v.play(); if (p && p.catch) p.catch(function () {}); } };
    document.addEventListener('click', window.__play, true);

    function finish() {
      if (window.__done) return;
      clearInterval(sampler);
      R.playedSeconds = R.maxCurrentTime;
      // reconnects counts the initial connect too; >1 means it rebuilt.
      var rebuilds = Math.max(0, R.reconnects - 1);
      R.rebuilds = rebuilds;
      // Reproduced iff it fell back to MJPEG, rebuilt repeatedly (the flash),
      // went black repeatedly, or never played most of the stream.
      var partial = R.streamSeconds && R.playedSeconds < 0.8 * R.streamSeconds;
      R.reproduced = R.mjpegFallback || rebuilds >= 2 || R.blackEvents >= 3 || partial;
      try { player && player.destroy && player.destroy(); } catch (e) {}
      render();
      window.__result = R;
      window.__done = true;
      document.title = 'DONE';
    }
    setTimeout(finish, maxMs);
  }).catch(function (e) {
    note('load-fail:' + e);
    window.__result = R; window.__done = true; document.title = 'DONE';
  });
  }
})();

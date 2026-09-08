// Replay a recorded fMP4 HEVC stream into MediaSource and measure decode health.
//
// The stream (stream.bin + stream.json) was captured from an OpenIPC/majestic
// camera's /ws/video endpoint: an init segment (ftyp+moov) followed by one
// moof+mdat fragment per frame, each with its arrival time. We feed them to a
// SourceBuffer in the same order and — by default — at the same cadence the
// camera delivered them, which is what the WebUI's Live page does. In real
// Safari the SourceBuffer is decoded by VideoToolbox, so this exercises the
// exact path OpenIPC/majestic-webui#335 reports flashing on H.265 Main + MSE.
//
// Query params:
//   ms=<n>       run for n ms then finish        (default 25000)
//   burst=1      append as fast as possible instead of at recorded cadence
//   noreinit=1   do NOT rebuild MediaSource on a decode error (bare decode test)
//   stream=<b>   base name of the recording      (default "stream")
//
// The verdict lands on window.__result and the title becomes "DONE" when the
// run ends, which is what run.py polls.
(function () {
  'use strict';
  var q = new URLSearchParams(location.search);
  var paced = !q.get('burst');
  var reinit = !q.get('noreinit');
  var maxMs = +(q.get('ms') || 25000);
  var base = q.get('stream') || 'stream';

  var R = {
    ua: navigator.userAgent, codec: null, paced: paced, reinit: reinit,
    fragments: 0, appended: 0, decodeErrors: 0, errorCodes: [], reinits: 0,
    blackFrames: 0, blackEvents: 0, stalls: 0, timeAdvances: 0,
    endedClean: false, maxCurrentTime: 0, playedSeconds: 0,
    firstErrorAtSec: null, mseSupported: null, canPlayType: null,
    samples: [], note: '', reproduced: null
  };
  // window.__result is published only when the run ends (see finish()), so a
  // poller can wait on it; window.__progress carries the live state meanwhile.
  window.__progress = R;
  window.__done = false;

  var video = document.getElementById('v');
  var canvas = document.getElementById('c');
  var ctx = canvas.getContext('2d', { willReadFrequently: true });
  var logEl = document.getElementById('log');
  function note(m) { R.note += m + ' | '; render(); }
  function render() {
    logEl.textContent = JSON.stringify({
      codec: R.codec, appended: R.appended + '/' + R.fragments,
      decodeErrors: R.decodeErrors, errorCodes: R.errorCodes, reinits: R.reinits,
      blackEvents: R.blackEvents, stalls: R.stalls, t: R.maxCurrentTime.toFixed(2),
      note: R.note.slice(-300)
    }, null, 1);
  }

  var man, buf, initSeg, frags = [], keyIdx = [];
  var ms, sb, fragIdx = 0, playStart = 0;

  R.mseSupported = ('MediaSource' in window);
  if (!R.mseSupported) { note('no MediaSource'); return finish(); }

  fetch(base + '.json').then(function (r) { return r.json(); }).then(function (m) {
    man = m; R.codec = m.codecString; R.fragments = m.fragments.length;
    R.canPlayType = video.canPlayType(m.mime);
    if (window.MediaSource && MediaSource.isTypeSupported) {
      R.mseTypeSupported = MediaSource.isTypeSupported(m.mime);
    }
    return fetch(base + '.bin').then(function (r) { return r.arrayBuffer(); });
  }).then(function (ab) {
    buf = ab;
    initSeg = buf.slice(0, man.initLength);
    man.fragments.forEach(function (f, i) {
      frags.push({ data: buf.slice(f.offset, f.offset + f.length), arrivalMs: f.arrivalMs, key: !!f.key });
      if (f.key) keyIdx.push(i);
    });
    start();
  }).catch(function (e) { note('load-fail:' + e); finish(); });

  function luma() {
    try {
      if (!video.videoWidth) return null;
      canvas.width = 64; canvas.height = 36;
      ctx.drawImage(video, 0, 0, 64, 36);
      var d = ctx.getImageData(0, 0, 64, 36).data, s = 0;
      for (var i = 0; i < d.length; i += 4) s += 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
      return s / (d.length / 4);
    } catch (e) { return null; }
  }

  function nextKeyFrom(idx) {
    for (var i = 0; i < keyIdx.length; i++) if (keyIdx[i] >= idx) return keyIdx[i];
    return frags.length;
  }

  function bufferedAhead() {
    try {
      var b = video.buffered;
      if (b && b.length) return b.end(b.length - 1) - video.currentTime;
    } catch (e) {}
    return 0;
  }

  // Safari allows muted autoplay, but under WebDriver a real user gesture is the
  // reliable trigger, so run.py clicks the page and this plays on that click too.
  window.__play = function () {
    try {
      video.muted = true;
      var p = video.play();
      if (p && p.catch) p.catch(function (e) { R.playTries = (R.playTries || 0) + 1; });
    } catch (e) {}
  };

  function setupMS(fromIdx) {
    fragIdx = fromIdx;
    ms = new MediaSource();
    video.src = URL.createObjectURL(ms);
    ms.addEventListener('sourceopen', function () {
      try { sb = ms.addSourceBuffer(man.mime); }
      catch (e) { note('addSourceBuffer-fail:' + e); return finish(); }
      appendOnce(initSeg, function () {
        playStart = performance.now();
        window.__play();  // and again on the WebDriver click / canplay below
        pump();
      });
    }, { once: true });
  }

  function appendOnce(data, cb) {
    function onEnd() { sb.removeEventListener('updateend', onEnd); cb && cb(); }
    sb.addEventListener('updateend', onEnd);
    try { sb.appendBuffer(data); }
    catch (e) { note('append-throw:' + e); sb.removeEventListener('updateend', onEnd); setTimeout(cb, 40); }
  }

  function pump() {
    if (window.__done) return;
    if (fragIdx >= frags.length) { try { ms.endOfStream(); } catch (e) {} return; }
    var f = frags[fragIdx];
    var wait = paced ? Math.max(0, f.arrivalMs - (performance.now() - playStart)) : 0;
    setTimeout(function () {
      if (window.__done) return;
      if (!sb || ms.readyState !== 'open') return;
      if (sb.updating) { setTimeout(pump, 20); return; }
      // Evict already-played data, the way a real MSE player does. Safari's
      // SourceBuffer quota is small, and this stream is large (all-keyframe
      // 2592x1520), so without eviction the buffer fills after a few seconds and
      // appends quietly stop — which looked like a decode stall but was not.
      try {
        var b0 = video.buffered;
        if (b0.length && b0.start(0) < video.currentTime - 4) {
          sb.remove(0, video.currentTime - 2);
          setTimeout(pump, 30); return;   // remove() is async (its own updateend)
        }
      } catch (e) {}
      // Don't overfill ahead while the video is paused (autoplay not yet
      // granted): hold until it drains, which it does once playback starts.
      if (bufferedAhead() > 8) { setTimeout(pump, 100); return; }
      appendOnce(f.data, function () { R.appended++; fragIdx++; render(); pump(); });
    }, wait);
  }

  function start() {
    video.addEventListener('error', function () {
      var code = video.error ? video.error.code : -1;
      R.decodeErrors++; R.errorCodes.push(code);
      if (R.firstErrorAtSec === null) R.firstErrorAtSec = +video.currentTime.toFixed(2);
      note('VIDEO_ERROR code=' + code + ' t=' + video.currentTime.toFixed(2));
      if (reinit && !window.__done) {
        R.reinits++;
        try { URL.revokeObjectURL(video.src); } catch (e) {}
        // MSE appends must resume at a keyframe: jump to the next IDR at/after here.
        setupMS(nextKeyFrom(fragIdx));
      }
    });
    video.addEventListener('waiting', function () { R.stalls++; });
    video.addEventListener('ended', function () { R.endedClean = true; });
    video.addEventListener('canplay', window.__play);
    document.addEventListener('click', window.__play, true);

    var lastBlack = false;
    var sampler = setInterval(function () {
      var t = video.currentTime;
      if (t > R.maxCurrentTime + 0.001) { R.timeAdvances++; R.maxCurrentTime = t; }
      var b = luma();
      if (b !== null) {
        var isBlack = b < 4;
        if (isBlack) R.blackFrames++;
        if (isBlack && !lastBlack) R.blackEvents++;
        lastBlack = isBlack;
        if (R.samples.length < 700) R.samples.push({ t: +t.toFixed(2), luma: +b.toFixed(1) });
      }
    }, 33);
    R._sampler = sampler;

    setupMS(0);
  }

  function finish() {
    if (window.__done) return;
    if (R._sampler) clearInterval(R._sampler);
    R.playedSeconds = R.maxCurrentTime;
    // Reproduced iff Safari faulted the decode or the picture kept going black.
    R.reproduced = (R.decodeErrors > 0) || (R.blackEvents >= 3);
    delete R._sampler;
    render();
    window.__result = R;   // publish before flipping __done so a poller sees both
    window.__done = true;
    document.title = 'DONE';
  }
  setTimeout(finish, maxMs);
})();

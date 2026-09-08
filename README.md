# safari-hevc-qa

Reproduce **real Safari** HEVC decode on a **GitHub-hosted macOS runner**, using
a prerecorded H.265 stream — so a bug that only shows in Safari's VideoToolbox
decoder can be exercised from CI, without a Mac on anyone's desk and without the
camera being reachable.

This is the Safari counterpart to
[`OpenIPC/chrome-hevc-qa`](https://github.com/OpenIPC/chrome-hevc-qa), which does
the same for Chrome + VA-API in Docker on Linux. Chrome on Linux cannot use
VideoToolbox, so it cannot answer "does this stream play in Safari?" — a macOS
runner can.

## Why it exists

[`OpenIPC/majestic-webui#335`](https://github.com/OpenIPC/majestic-webui/issues/335):
on the Live page with **Main + MSE**, the H.265 video flashes every ~2 s
(video → black → video) **in Safari**. Chrome plays the same camera cleanly, so
the fault is in Safari's decode of the stream, and there is no Safari on Linux to
test it. This harness closes that gap.

## What's in the box

```
web/mse-hevc.html   the page: a <video> fed by MediaSource
web/player.js       replays the recording into a SourceBuffer, measures health
web/stream.bin      a recorded fMP4 HEVC stream (init segment + one fragment/frame)
web/stream.json     manifest: codec string, per-fragment offset/length/dts/arrival/key
run.py              drives real Safari via safaridriver, prints the verdict
.github/workflows/safari-hevc.yml   runs it on macos-14 and macos-15
```

### The recording

`web/stream.bin` was captured from the `/ws/video` MSE endpoint of the lab camera
`hi3516ev300-imx335.dlab.torturelabs.com` — **H.265 Main, `hvc1.1.6.L153.B0`,
2592×1520, 20 fps, all-keyframe GOP of 1 s, ~22 s**. The camera has **no lens**,
so the picture is a flat sensor field and the recording is safe to publish. It is
the byte stream the WebUI would hand to `SourceBuffer.appendBuffer`, replayed in
order and (by default) at the cadence the camera delivered it.

To re-record or record a different configuration, point `tools/record.py` (the
capture script) at any majestic camera's `/ws/video?stream=0`.

## What it measures

The page plays the stream through MSE and watches, once per frame:

- **`video.error`** — Safari faulting the decode (`code=3` is `MEDIA_ERR_DECODE`);
- **black frames** — the picture drawn to a canvas and its mean luma read, so a
  flash to black is counted even when no error fires;
- **stalls**, **currentTime progress**, and how much of the stream actually played.

On a decode error it rebuilds the MediaSource from the next keyframe, the way the
WebUI's player does — which is what turns a periodic decode fault into the
reported *flash* rather than a dead player. `noreinit=1` disables that for a bare
"does it decode at all" test.

`window.__result` carries the verdict; `run.py` reads it and exits **0** for clean
playback, **1** if the fault reproduced (decode error, or ≥3 black events), **2**
on a harness failure. The full per-frame luma trace is uploaded as `result.json`.

## Run it

**In CI:** push, or use the **Run workflow** button (`workflow_dispatch`). The
`params` input is appended to the page query string — e.g. `burst=1` to append as
fast as possible, `noreinit=1` for a bare decode test, `ms=30000` to run longer.

**On a local Mac:**

```sh
sudo safaridriver --enable
python3 -m pip install 'selenium>=4.20'
( cd web && python3 -m http.server 8000 & )
python3 run.py http://localhost:8000/mse-hevc.html
```

**Plumbing check on Linux (Chrome + VA-API, not Safari):** the page itself runs
anywhere HEVC MSE is supported; on Linux you can confirm the replay works with
`OpenIPC/chrome-hevc-qa`. Chrome playing it clean only proves the harness is
sound — it says nothing about Safari, which is the whole point of the macOS job.

## What it found (first runs, Safari 26.6)

Running the committed recording — H.265 Main, 2592×1520, ~2.2 Mbit/s,
all-keyframe (GOP 1 s) — on GitHub's macOS runners:

| runner | macOS / Safari | result |
| --- | --- | --- |
| `macos-15` | 15.7.9 / 26.6.1 | HEVC MSE **supported**, but the SourceBuffer **freezes after ~2.8 s** with **no `video.error`** — `currentTime` stops, `updateend` stops — and rebuilding it (as the WebUI does) stalls again within a second. Six freeze/rebuild cycles in the run: the reported *flash*. |
| `macos-14` | 14.8.9 / 26.6 | HEVC MSE **not supported at all**: `canPlayType` empty, `MediaSource.isTypeSupported` false, `addSourceBuffer` throws `NotSupportedError`. |

For contrast, Chrome + VA-API (via `OpenIPC/chrome-hevc-qa`) plays the **same
recording** end to end — 421/421 fragments, 22 s, zero freezes, zero decode
errors. So the fault is Safari's MSE HEVC path, not the camera's stream (which is
conformant H.265 Main) and not the harness.

### Root cause and fix (isolated with this harness)

The trigger is **append frequency**, not any stream property: the WebUI appended
one fMP4 fragment per `appendBuffer` (~20–30/s, one per frame), and Safari's
SourceBuffer wedges under that. Coalescing a handful of fragments into one append
fixes it — on `macos-15` the modes compare directly:

| `?params=` | Safari macOS 15 |
| --- | --- |
| `chunk=1` (per-frame, the old behaviour) | stalls at ~2.8 s, 6 flash cycles |
| `chunk=5` | plays the full 22 s, 0 stalls |
| `gop=1` (coalesce a whole GOP) | plays the full 22 s, 0 stalls |

The WebUI fix is `OpenIPC/majestic-webui#411`: for HEVC, batch ~5 fragments per
`appendBuffer` (H.264 left per-frame for its low-latency path). Use `chunk=` /
`gop=` here to re-confirm or to size the batch for a new Safari.

### Before/after with the real WebUI player

`webui.html` runs the **actual `preview.js`** (`window.MajesticVideo`) against a
stubbed WebSocket replaying the recording, so the shipped code — its queue, its
coalescing, its reconnect logic — is what Safari runs. Same harness, same stream,
same macOS-15 Safari 26.6.1, only the player build differs:

| `?page=webui.html&player=` | result |
| --- | --- |
| `preview-unfixed.js` (master, per-frame) | plays, then **stalls at 3.6 s** |
| `preview.js` (the #411 fix, default) | **full 22 s, 0 rebuilds, clean** |

Because a reproduced fault exits non-zero, the macOS jobs are **red while the bug
is present** and will go **green if a future Safari plays the stream through** —
i.e. this doubles as a regression watch.

## Reading the result

- **`reproduced: true`** with `errorCodes: [3, …]` — Safari's decoder rejected the
  stream; the camera's H.265 Main / MSE output is what #335 is about.
- **`reproduced: false`, played through** — this stream decodes cleanly on this
  Safari version. That narrows the trigger (a different resolution, frame rate,
  or a regression since the recording) rather than clearing it.

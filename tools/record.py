#!/usr/bin/env python3
"""Record a majestic camera's /ws/video MSE stream into web/stream.{bin,json}.

Connects to ws://<host>/ws/video?stream=<n> with HTTP Basic auth, keeps the
fMP4 init segment and every moof+mdat fragment, and writes a replayable pair:

  stream.bin   init segment followed by all fragments, concatenated
  stream.json  { codecString, mime, timescale, initLength,
                 fragments: [ { offset, length, dts, arrivalMs, key } ] }

Usage:
  tools/record.py <host> [stream=0] [seconds=22] [user=root] [pass=123456] [outdir=web]

The camera used for the committed recording had no lens at the time, so its
picture carried nothing private. It has since been fitted with one. Look at a
snapshot before recording and publishing from ANY camera, that one included.
"""
import base64
import json
import os
import socket
import struct
import sys
import time


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "hi3516ev300-imx335.dlab.torturelabs.com"
    stream = sys.argv[2] if len(sys.argv) > 2 else "0"
    dur = float(sys.argv[3]) if len(sys.argv) > 3 else 22.0
    user = sys.argv[4] if len(sys.argv) > 4 else "root"
    pw = sys.argv[5] if len(sys.argv) > 5 else "123456"
    outdir = sys.argv[6] if len(sys.argv) > 6 else "web"
    port = 80
    path = "/ws/video?stream=%s" % stream

    key = base64.b64encode(os.urandom(16)).decode()
    auth = base64.b64encode(("%s:%s" % (user, pw)).encode()).decode()
    s = socket.create_connection((host, port), 8)
    s.settimeout(6)
    s.sendall(("GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\nAuthorization: Basic %s\r\n\r\n"
               % (path, host, key, auth)).encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        buf += s.recv(4096)
    assert b"101" in buf.split(b"\r\n")[0], buf.split(b"\r\n")[0]
    _, _, rest = buf.partition(b"\r\n\r\n")
    data = bytearray(rest)

    def rf():
        while len(data) < 2:
            data.extend(s.recv(16384))
        ln = data[1] & 0x7f
        off = 2
        if ln == 126:
            while len(data) < 4:
                data.extend(s.recv(16384))
            ln = struct.unpack(">H", data[2:4])[0]
            off = 4
        elif ln == 127:
            while len(data) < 10:
                data.extend(s.recv(16384))
            ln = struct.unpack(">Q", data[2:10])[0]
            off = 10
        op = data[0] & 0x0f
        while len(data) < off + ln:
            data.extend(s.recv(16384))
        p = bytes(data[off:off + ln])
        del data[:off + ln]
        return op, p

    def boxes(b):
        i = 0
        while i + 8 <= len(b):
            sz = struct.unpack(">I", b[i:i + 4])[0]
            t = b[i + 4:i + 8]
            if sz < 8:
                break
            yield t, b[i + 8:i + sz]
            i += sz

    def find(b, pth):
        cur = b
        for w in pth:
            nx = None
            for t, body in boxes(cur):
                if t == w:
                    nx = body
                    break
            if nx is None:
                return None
            cur = nx
        return cur

    codec_string = None
    timescale = None
    text_init = None
    init = None
    manifest = []
    blob = bytearray()
    t0 = time.time()
    while time.time() - t0 < dur:
        try:
            op, pl = rf()
        except socket.timeout:
            if init is not None and len(manifest) > 30:
                break
            continue
        if op == 8:
            break
        if op == 1:
            try:
                j = json.loads(pl.decode())
                codec_string = j.get("codecString", codec_string)
                if j.get("type") == "init" and text_init is None:
                    text_init = pl.decode()   # kept verbatim to replay to onInit
            except Exception:
                pass
            continue
        if op != 2:
            continue
        if init is None and (b"ftyp" in pl[:16] or b"moov" in pl):
            init = pl
            mdhd = find(init, [b"moov", b"trak", b"mdia", b"mdhd"])
            if mdhd:
                timescale = (struct.unpack(">I", mdhd[20:24])[0] if mdhd[0] == 1
                             else struct.unpack(">I", mdhd[12:16])[0])
            continue
        if init is None:
            continue
        tfdt = find(pl, [b"moof", b"traf", b"tfdt"])
        dts = None
        if tfdt:
            dts = struct.unpack(">Q", tfdt[4:12])[0] if tfdt[0] == 1 else struct.unpack(">I", tfdt[4:8])[0]
        is_key = False
        for t, body in boxes(pl):
            if t == b"mdat":
                i = 0
                while i + 4 <= len(body):
                    l = struct.unpack(">I", body[i:i + 4])[0]
                    i += 4
                    if l == 0 or i + l > len(body):
                        break
                    if ((body[i] >> 1) & 0x3f) in (19, 20, 21):
                        is_key = True
                    i += l
                break
        manifest.append({"length": len(pl), "dts": dts,
                         "arrivalMs": round((time.time() - t0) * 1000), "key": is_key})
        blob += pl

    if init is None:
        print("no init segment received — is the stream flowing?")
        return 1

    out = bytearray(init)
    frags = []
    p = 0
    for m in manifest:
        frags.append({"offset": len(out), "length": m["length"], "dts": m["dts"],
                      "arrivalMs": m["arrivalMs"], "key": m["key"]})
        out += blob[p:p + m["length"]]
        p += m["length"]

    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "stream.bin"), "wb") as f:
        f.write(out)
    hdr = {"codecString": codec_string, "mime": 'video/mp4; codecs="%s"' % codec_string,
           "timescale": timescale, "initLength": len(init), "fragments": frags, "textInit": text_init,
           "source": "%s /ws/video?stream=%s (no lens)" % (host, stream)}
    with open(os.path.join(outdir, "stream.json"), "w") as f:
        json.dump(hdr, f)
    print("codecString=%s timescale=%s fragments=%d bytes=%d durationMs=%s"
          % (codec_string, timescale, len(frags), len(out), frags[-1]["arrivalMs"] if frags else 0))
    return 0


if __name__ == "__main__":
    sys.exit(main())

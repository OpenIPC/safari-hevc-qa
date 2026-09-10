#!/usr/bin/env python3
"""The camera's video bitstream over an RTCDataChannel, from real Safari.

The page-side probe (web/dc-probe.js, shared with the Chrome harness at
OpenIPC/chrome-hevc-qa) offers a data-only PeerConnection on the camera's
WebRTC signalling socket, checks every message against the published
header, asks for a keyframe halfway and reports. This driver signs in on
the camera's own login page, injects the probe and prints the summary.

Usage: run-dc.py <camera-url> [seconds] [stream] [mode] [ice-json]
  ice-json: an RTCPeerConnection iceServers array as JSON, for a camera
  reachable only through a relay; CAMERA_USER / CAMERA_PASS sign in.
Exit 1 on a failed check, 0 on pass, 2 on a harness failure.
"""
import json
import os
import sys

URL = sys.argv[1]
SECONDS = int(sys.argv[2]) if len(sys.argv) > 2 else 14
STREAM = int(sys.argv[3]) if len(sys.argv) > 3 else 1
MODE = sys.argv[4] if len(sys.argv) > 4 else "negotiated"
ICE = json.loads(sys.argv[5]) if len(sys.argv) > 5 and sys.argv[5] else []

try:
    from selenium import webdriver
    from selenium.webdriver.safari.options import Options
except Exception as e:  # noqa
    print("HARNESS: selenium unavailable: %s" % e)
    sys.exit(2)


def main():
    probe = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "dc-probe.js")).read()
    try:
        driver = webdriver.Safari(options=Options())
    except Exception as e:  # noqa
        print("HARNESS: could not start Safari WebDriver: %s" % e)
        return 2
    # Whatever happens past this point, the Safari session is closed and a
    # failure of the harness itself is reported as one, not raised.
    try:
        driver.set_script_timeout(SECONDS + 30)
        driver.get(URL.rstrip("/") + "/login.html")
        status = driver.execute_async_script(
            "const [u, p, done] = arguments;"
            "fetch('/login', {method: 'POST', credentials: 'same-origin',"
            " headers: {'Content-Type': 'application/x-www-form-urlencoded'},"
            " body: new URLSearchParams({username: u, password: p}).toString()})"
            ".then(r => done(r.status), () => done(-1));",
            os.environ.get("CAMERA_USER", ""), os.environ.get("CAMERA_PASS", ""))
        print("sign-in: HTTP %s" % status)
        if status != 200:
            print("FAIL: sign-in refused")
            return 1
        driver.execute_script(probe)
        out = driver.execute_async_script(
            "const [s, st, m, ice, done] = arguments;"
            "window.__dcProbe(s, st, m, {iceServers: ice}).then(done, e => done({error: String(e)}));",
            SECONDS, STREAM, MODE, ICE)
        ua = driver.execute_script("return navigator.userAgent")
    except Exception as e:  # noqa
        print("HARNESS: %s" % e)
        return 2
    finally:
        driver.quit()
    if not isinstance(out, dict) or "error" in out:
        print("HARNESS: probe failed: %s" % out)
        return 2
    print(json.dumps({k: v for k, v in out.items() if k != "answer"}))
    cam_up = any("dc=up" in l for l in out.get("stats", []))
    if out.get("answerDeclined"):
        v = "FAIL: the camera declined the data section"
    elif not out.get("answerHasData"):
        v = "FAIL: no answer with an application section: %s" % out.get("errors")
    elif out.get("openAt") is None:
        v = "FAIL: the channel never opened; ice %s errors %s" % (out.get("ice"), out.get("errors"))
    elif out.get("bad") or out.get("badBox"):
        v = "FAIL: %s bad header(s), %s wrong first box(es)" % (out.get("bad"), out.get("badBox"))
    elif out["kinds"]["init"] < 1 or out["kinds"]["initSeg"] < 1:
        v = "FAIL: no init messages"
    elif out.get("frames", 0) < 10:
        v = "FAIL: only %s frames" % out.get("frames")
    elif not cam_up:
        v = "FAIL: the camera never reported dc=up"
    elif out.get("initAfterAsk") is None or out.get("keyframeAfterAsk") is None:
        v = "FAIL: the keyframe request was not answered"
    else:
        q = out.get("queueMs", {})
        v = ("PASS: Safari %s channel open at %sms, first message at %sms, %s frames (%s key) at %s fps / %s kbps, "
             "%s hole(s), %s gap(s), %s late, %s with prft, %s part(s), queue p95 %sms; init %sms and keyframe %sms after "
             "the request; pair %s/%s" % (MODE, out.get("openAt"), out.get("firstAt"), out.get("frames"), out.get("keyframes"),
             out.get("fps"), out.get("kbps"), out.get("seqHoles"), out.get("gaps"), out.get("late"), out.get("prft"),
             out.get("multipart"), q.get("p95"), out.get("initAfterAsk"), out.get("keyframeAfterAsk"),
             (out.get("rtc") or {}).get("pair", {}).get("localType"), (out.get("rtc") or {}).get("pair", {}).get("remoteType")))
    print(ua)
    print(v)
    return 0 if v.startswith("PASS") else 1


if __name__ == "__main__":
    sys.exit(main())

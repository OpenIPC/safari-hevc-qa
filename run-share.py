#!/usr/bin/env python3
"""A camera share link opened in real Safari: does the camera admit it?

A share link (https://<id>.share.openipc.cloud/#<secret>) is opened as a
guest would open it. The share page connects to the camera over WebRTC, the
two prove the share's key to each other over a data channel, and the camera
admits the page (WELCOME). This driver waits for that, then prints the
page's own trace of how it got there -- the selected ICE pair, the handshake
it used -- and the verdict.

The link carries the share's secret in its fragment, so it comes from the
SHARE_LINK environment variable (a repository secret), never a workflow
input or an argument that a log would show. Use a short-lived, view-scoped
share and end it afterwards.

Usage: SHARE_LINK=... run-share.py [timeout-seconds]
Exit 1 on a failed check, 0 on pass, 2 on a harness failure.
"""
import json
import os
import sys

LINK = os.environ.get("SHARE_LINK", "")
TIMEOUT = int(sys.argv[1]) if len(sys.argv) > 1 else 60

try:
    from selenium import webdriver
    from selenium.webdriver.safari.options import Options
except Exception as e:  # noqa
    print("HARNESS: selenium unavailable: %s" % e)
    sys.exit(2)


def main():
    if not LINK:
        print("HARNESS: SHARE_LINK is not set")
        return 2
    try:
        driver = webdriver.Safari(options=Options())
    except Exception as e:  # noqa
        print("HARNESS: could not start Safari WebDriver: %s" % e)
        return 2
    try:
        driver.set_script_timeout(TIMEOUT + 15)
        driver.get(LINK)
        out = driver.execute_async_script(
            "const [limit, done] = arguments;"
            "const t0 = Date.now();"
            "(function wait() {"
            "  if (window.__shareReady || window.__shareError || Date.now() - t0 > limit * 1000) {"
            "    const r = {ready: !!window.__shareReady, error: window.__shareError || null, trace: []};"
            "    const s = document.querySelector('script[type=module]');"
            "    if (!s) { r.diag = 'no share page module on ' + location.href; done(r); return; }"
            "    import(new URL('./diag.js', s.src).href).then((m) => {"
            "      r.trace = m.report().split('\\n').filter((l) => /selected pair|WELCOME|one round trip|CHALLENGE|shown to the guest/.test(l)).map((l) => l.trim());"
            "      done(r);"
            "    }, (e) => { r.diag = String(e); done(r); });"
            "  } else setTimeout(wait, 200);"
            "})();",
            TIMEOUT)
        ua = driver.execute_script("return navigator.userAgent")
    except Exception as e:  # noqa
        print("HARNESS: %s" % e)
        return 2
    finally:
        driver.quit()
    print(json.dumps(out))
    print(ua)
    if out.get("ready"):
        # Name the handshake only from the page's own trace; without the
        # trace the camera's admission is still proven, its route is not.
        trace = out.get("trace") or []
        if any("one round trip" in l for l in trace):
            how = "one round trip"
        elif any("CHALLENGE" in l for l in trace):
            how = "challenge"
        else:
            how = "handshake unknown: %s" % (out.get("diag") or "no handshake line in the trace")
        print("PASS: Safari admitted to the shared camera (%s)" % how)
        return 0
    print("FAIL: not admitted: %s" % (out.get("error") or out.get("diag") or "timed out"))
    return 1


if __name__ == "__main__":
    sys.exit(main())

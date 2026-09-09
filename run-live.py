#!/usr/bin/env python3
"""Drive a Live-page harness in real Safari N times and aggregate startup
flickers. The #335 residual is intermittent on refresh, so one load proves
little; this reloads N times and reports how often the picture painted and then
went black again (a startup flicker).

Usage: run-live.py <url> [reloads] [per-run-timeout-s]
Exit 1 if any run flickered, 0 if none did, 2 on harness failure.
"""
import json
import sys
import time

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000/live/livepage.html"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 12
TIMEOUT = int(sys.argv[3]) if len(sys.argv) > 3 else 20

try:
    from selenium import webdriver
    from selenium.webdriver.safari.options import Options
    from selenium.webdriver.common.by import By
except Exception as e:  # noqa
    print("HARNESS: selenium unavailable: %s" % e)
    sys.exit(2)


def main():
    try:
        driver = webdriver.Safari(options=Options())
    except Exception as e:  # noqa
        print("HARNESS: could not start Safari WebDriver: %s" % e)
        return 2
    driver.set_window_size(1000, 760)

    results = []
    for k in range(N):
        try:
            driver.get(URL)
        except Exception as e:  # noqa
            print("run %d: navigate failed: %s" % (k + 1, e))
            results.append(None)
            continue
        # A real gesture for autoplay, then nudge play().
        time.sleep(0.6)
        try:
            driver.find_element(By.TAG_NAME, "body").click()
        except Exception:
            pass
        try:
            driver.execute_script("window.__play && window.__play();")
        except Exception:
            pass
        r, deadline = None, time.time() + TIMEOUT
        while time.time() < deadline:
            try:
                r = driver.execute_script("return window.__result || null;")
                if r:
                    break
            except Exception:
                pass
            time.sleep(0.4)
        results.append(r)
        if r:
            print("run %2d: painted=%s firstPaint=%sms blackAfterPaint=%s flicker=%s %s" % (
                k + 1, r.get("startedPaint"), r.get("firstPaintMs"),
                r.get("blackEventsAfterPaint"), r.get("reproduced"), r.get("note", "")))
        else:
            print("run %2d: no result" % (k + 1))
            if k == 0:  # diagnose the first stall in detail
                try:
                    diag = driver.execute_script(
                        "return {title:document.title, prog:(typeof window.__progress), "
                        "play:(typeof window.__play), err:(window.__err||[]).slice(0,8), "
                        "videos:document.querySelectorAll('video').length, "
                        "media:document.querySelectorAll('.mj-stage-media').length, "
                        "hasChain:(typeof window.MajesticChain), hasVideo:(typeof window.MajesticVideo), "
                        "badge:(document.getElementById('mj-badge')||{}).textContent};")
                    print("   DIAG: " + json.dumps(diag))
                except Exception as e:  # noqa
                    print("   DIAG failed: %s" % e)

    driver.quit()

    for r in results:
        if isinstance(r, dict):
            r.pop("samples", None)
    with open("result.json", "w") as f:
        json.dump(results, f, indent=1)

    painted = sum(1 for r in results if r and r.get("startedPaint"))
    flick = sum(1 for r in results if r and r.get("reproduced"))
    print("\nAGGREGATE over %d loads: painted %d, startup-flickered %d" % (len(results), painted, flick))
    if flick:
        print("RESULT: REPRODUCED — %d/%d Safari loads flickered after the first frame." % (flick, len(results)))
        return 1
    if painted == 0:
        print("RESULT: INCONCLUSIVE — the picture never painted (autoplay/harness).")
        return 2
    print("RESULT: clean — %d/%d loads painted, none flickered after the first frame." % (painted, len(results)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

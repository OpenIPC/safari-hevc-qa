#!/usr/bin/env python3
"""Drive real Safari through a recorded-HEVC MSE playback and print the verdict.

Loads web/mse-hevc.html in Safari via safaridriver (WebDriver), waits for the
in-page harness to finish, reads window.__result, prints it as JSON and exits:

  0  clean playback  (the defect did NOT reproduce on this Safari/stream)
  1  reproduced      (Safari faulted the decode or the picture went black)
  2  harness failure  (could not drive Safari, page never finished, …)

This only means anything on a machine with real Safari — a GitHub-hosted macOS
runner, or a local Mac. Chrome/Linux cannot exercise VideoToolbox HEVC.
"""
import json
import sys
import time

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000/mse-hevc.html"
TIMEOUT = int(sys.argv[2]) if len(sys.argv) > 2 else 90

try:
    from selenium import webdriver
    from selenium.webdriver.safari.options import Options
except Exception as e:  # noqa
    print("HARNESS: selenium not available: %s" % e)
    sys.exit(2)


def main():
    opts = Options()
    try:
        driver = webdriver.Safari(options=opts)
    except Exception as e:  # noqa
        print("HARNESS: could not start Safari WebDriver: %s" % e)
        print("Did the workflow run `sudo safaridriver --enable`?")
        return 2

    driver.set_window_size(900, 780)
    try:
        driver.get(URL)
    except Exception as e:  # noqa
        print("HARNESS: navigate failed: %s" % e)
        driver.quit()
        return 2

    # A real user gesture is the reliable autoplay trigger under WebDriver: click
    # the video (the page plays on click). Muted, so the policy allows it.
    try:
        from selenium.webdriver.common.by import By
        time.sleep(1.0)
        el = driver.find_element(By.ID, "v")
        el.click()
    except Exception:
        pass
    try:
        driver.execute_script("window.__play && window.__play();")
    except Exception:
        pass

    # The page sets document.title to "DONE" when the run ends.
    deadline = time.time() + TIMEOUT
    result = None
    while time.time() < deadline:
        try:
            done = driver.execute_script("return window.__done === true;")
            if done:
                result = driver.execute_script("return window.__result;")
                break
        except Exception:
            pass
        time.sleep(1)

    if result is None:
        try:
            result = driver.execute_script("return window.__result || null;")
        except Exception:
            result = None
        driver.quit()
        if result is None:
            print("HARNESS: page never produced a result within %ds" % TIMEOUT)
            return 2
        print("HARNESS: page did not finish in time; partial result follows")

    driver.quit()

    # Trim the per-frame luma trace for the console; keep it in the JSON artifact.
    trace = result.pop("samples", []) if isinstance(result, dict) else []
    print(json.dumps(result, indent=2))
    try:
        with open("result.json", "w") as f:
            result["samples"] = trace
            json.dump(result, f, indent=1)
    except Exception:
        pass

    if not isinstance(result, dict):
        print("HARNESS: malformed result")
        return 2

    codec = result.get("codec")
    reproduced = result.get("reproduced")
    played = result.get("playedSeconds", 0) or 0
    errs = result.get("decodeErrors", 0)
    blk = result.get("blackEvents", 0)
    appended = result.get("appended", 0)
    total = result.get("fragments", 0)
    mse_ok = result.get("mseTypeSupported")

    # A genuine finding: this Safari cannot decode HEVC in MSE at all.
    if mse_ok is False or result.get("canPlayType") == "":
        print("\nRESULT: unsupported — this Safari cannot play %s in MSE "
              "(canPlayType=%r, MediaSource.isTypeSupported=%r)."
              % (codec, result.get("canPlayType"), mse_ok))
        return 1

    if reproduced:
        print("\nRESULT: REPRODUCED — %d decode error(s) %s, %d black event(s), "
              "played %.1fs of stream." % (errs, result.get("errorCodes"), blk, played))
        return 1

    # Supported, no error — but did it actually play? If not, the run proves
    # nothing (autoplay refused, page never advanced): inconclusive, not clean.
    if played < 2.0:
        print("\nRESULT: INCONCLUSIVE — HEVC MSE is supported but playback never "
              "advanced (played %.2fs, appended %d/%d). Autoplay/harness issue, "
              "not a verdict on the stream." % (played, appended, total))
        return 2

    print("\nRESULT: clean — played %.1fs, appended %d/%d, no decode errors, "
          "%d black event(s). This stream does not reproduce the fault on this "
          "Safari." % (played, appended, total, blk))
    return 0


if __name__ == "__main__":
    sys.exit(main())

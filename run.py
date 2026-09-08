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

    # Muted autoplay is allowed by Safari, but nudge it in case automation is
    # stricter — harmless if the page already started.
    try:
        driver.execute_script(
            "var v=document.getElementById('v');"
            "if(v){v.muted=true;var p=v.play&&v.play();if(p&&p.catch)p.catch(function(){});}")
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
    played = result.get("playedSeconds", 0)
    errs = result.get("decodeErrors", 0)
    blk = result.get("blackEvents", 0)

    if result.get("canPlayType") == "" and result.get("mseTypeSupported") is False:
        print("\nRESULT: Safari reports it cannot play %s at all "
              "(canPlayType empty, MSE type unsupported)." % codec)
        return 1

    if reproduced:
        print("\nRESULT: REPRODUCED — %d decode error(s) %s, %d black event(s), "
              "played %.1fs of stream." % (errs, result.get("errorCodes"), blk, played))
        return 1

    print("\nRESULT: clean — no decode errors, %d black event(s), played %.1fs. "
          "This stream does not reproduce the fault on this Safari." % (blk, played))
    return 0


if __name__ == "__main__":
    sys.exit(main())

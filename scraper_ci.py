"""Incremental headless scraper for GitHub Actions.

The site is a single-page app: every /test/N URL returns the same static
shell (same HTTP status, same bytes) and the real content is rendered by
client-side JS afterwards. So HTTP status / "is it empty" checks can't tell
a real test apart from a nonexistent one.

Fix: fetch a test number far outside any plausible range once at the start
and use whatever it renders as the "doesn't exist" baseline text. Any test
whose rendered text matches that baseline is treated as missing.
"""
import json
import os
import sys
import time
from playwright.sync_api import sync_playwright

BASE = "https://li3.di.uminho.pt/test/{}"
OUT = "li3_dump.json"
INITIAL_MAX = 128       # always sweep tests 1..128
REFRESH_LAST = 5        # re-fetch the newest N completed tests on every run
PROBE_MISSES = 10       # stop probing after this many consecutive missing/failed tests
MAX_TEST = 2000         # safety cap
BASELINE_TEST = 999999  # assumed to never exist; used to learn what "missing" looks like
TIME_BUDGET_SEC = 40 * 60  # stop early (but still save) if a run runs this long

start_time = time.time()


def load_existing():
    if not os.path.exists(OUT):
        return {}
    with open(OUT, encoding="utf-8") as f:
        return {int(k): v for k, v in json.load(f).items()}


def is_done(rec):
    return rec is not None and "error" not in rec and bool(rec.get("text", "").strip())


def normalize(text):
    return " ".join(text.split())


def render(page, n):
    """Loads /test/n and returns (inner_text, json_captured) or raises."""
    captured = []

    def on_response(resp):
        try:
            if "json" in (resp.headers.get("content-type") or ""):
                captured.append({"url": resp.url, "data": resp.json()})
        except Exception:
            pass

    page.on("response", on_response)
    try:
        page.goto(BASE.format(n), wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        page.wait_for_timeout(800)
        return page.inner_text("body").strip(), captured
    finally:
        page.remove_listener("response", on_response)


existing = load_existing()
out = dict(existing)

done = {n for n, rec in existing.items() if is_done(rec)}
refresh = set(sorted(done)[-REFRESH_LAST:]) if REFRESH_LAST else set()
todo = [n for n in range(1, INITIAL_MAX + 1) if n not in done or n in refresh]
print(f"Have {len(done)} good tests; sweeping {len(todo)} of 1..{INITIAL_MAX}.")

attempted = 0
errors = 0


def time_left():
    return time.time() - start_time < TIME_BUDGET_SEC


try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Learn what a nonexistent test looks like.
        try:
            baseline_text, _ = render(page, BASELINE_TEST)
            baseline_norm = normalize(baseline_text)
            print(f"Baseline (test {BASELINE_TEST}) captured, {len(baseline_norm)} chars.")
        except Exception as e:
            baseline_norm = None
            print(f"Could not load baseline test: {e}. Falling back to 'empty page' only.")

        def is_missing(text):
            norm = normalize(text)
            if not norm:
                return True
            if baseline_norm is not None and norm == baseline_norm:
                return True
            return False

        def scrape(n):
            global attempted, errors
            attempted += 1
            try:
                text, captured = render(page, n)
            except Exception as e:
                errors += 1
                out[n] = {"error": str(e)}
                print(f"/test/{n}: error ({str(e).splitlines()[0][:120]})")
                return "error"
            if is_missing(text):
                print(f"/test/{n}: missing")
                return "missing"
            out[n] = {"text": text, "json": captured}
            print(f"/test/{n}: ok")
            return "ok"

        for n in todo:
            if not time_left():
                print("Time budget reached during sweep; stopping early.")
                break
            scrape(n)

        # Probe upward for new tests.
        if time_left():
            n = max(INITIAL_MAX, max(out, default=0)) + 1
            misses = 0
            last_ok = max(out, default=0)
            while n <= MAX_TEST and misses < PROBE_MISSES and time_left():
                status = scrape(n)
                if status == "ok":
                    misses = 0
                    last_ok = n
                else:
                    misses += 1
                n += 1
            print(f"Probe stopped at /test/{n - 1}; last good test: {last_ok}.")

        browser.close()
finally:
    # Always save whatever was gathered, even if something above raised or ran out of time.
    if attempted and errors > attempted // 2:
        print("Too many errors; not overwriting the existing dump.")
        sys.exit(1)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(out.items())), f, ensure_ascii=False, indent=2)
    print(f"Saved {OUT} with {len(out)} tests.")
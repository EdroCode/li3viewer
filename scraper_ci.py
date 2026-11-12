"""Incremental headless scraper for GitHub Actions.

- Sweeps tests 1..INITIAL_MAX, skipping tests already saved in li3_dump.json.
  Failed tests and the newest REFRESH_LAST tests are fetched again.
- Then probes upward. It stops after PROBE_MISSES consecutive tests that are
  missing or failed, and logs why each one was rejected.
"""
import json
import os
import sys
from playwright.sync_api import sync_playwright

BASE = "https://li3.di.uminho.pt/test/{}"
OUT = "li3_dump.json"
INITIAL_MAX = 128   # always sweep tests 1..128
REFRESH_LAST = 5    # re-fetch the newest N completed tests on every run
PROBE_MISSES = 10   # stop probing after this many consecutive missing/failed tests
MAX_TEST = 5000     # safety cap


def load_existing():
    if not os.path.exists(OUT):
        return {}
    with open(OUT, encoding="utf-8") as f:
        return {int(k): v for k, v in json.load(f).items()}


def is_done(rec):
    return rec is not None and "error" not in rec and bool(rec.get("text", "").strip())


def fetch(page, n):
    """Returns (status, record, reason). status: 'ok' | 'missing' | 'error'."""
    captured = []

    def on_response(resp):
        try:
            if "json" in (resp.headers.get("content-type") or ""):
                captured.append({"url": resp.url, "data": resp.json()})
        except Exception:
            pass

    page.on("response", on_response)
    try:
        r = page.goto(BASE.format(n), wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass  # slow background requests are fine; we still have the page
        page.wait_for_timeout(1000)
        status_code = r.status if r else None
        if status_code is not None and status_code >= 400:
            return "missing", None, f"HTTP {status_code}"
        text = page.inner_text("body").strip()
        if not text:
            return "missing", None, "empty page"
        return "ok", {"text": text, "json": list(captured)}, ""
    except Exception as e:
        return "error", {"error": str(e)}, str(e).splitlines()[0][:120]
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


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    def scrape(n):
        global attempted, errors
        attempted += 1
        status, rec, reason = fetch(page, n)
        if status == "ok":
            out[n] = rec
        elif status == "error":
            errors += 1
            out[n] = rec
        print(f"/test/{n}: {status}{' (' + reason + ')' if reason else ''}")
        return status

    for n in todo:
        scrape(n)

    # Probe upward. Keep going through isolated gaps; stop after PROBE_MISSES in a row.
    n = max(INITIAL_MAX, max(out, default=0)) + 1
    misses = 0
    last_ok = max(out, default=0)
    while n <= MAX_TEST and misses < PROBE_MISSES:
        status = scrape(n)
        if status == "ok":
            misses = 0
            last_ok = n
        else:
            misses += 1
        n += 1
    print(f"Probe stopped at /test/{n - 1}; last good test: {last_ok}.")

    browser.close()

# If most requests failed, the site is probably down or blocking us: keep the old dump.
if attempted and errors > attempted // 2:
    print("Too many errors; not overwriting the existing dump.")
    sys.exit(1)

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(dict(sorted(out.items())), f, ensure_ascii=False, indent=2)
print(f"Saved {OUT} with {len(out)} tests.")
"""Incremental headless scraper for GitHub Actions.

- First run (no li3_dump.json): fetches tests 1..INITIAL_MAX.
- Later runs: loads the existing dump, fetches only tests that are missing
  or failed last time, re-checks the newest REFRESH_LAST tests, and then
  probes upward for new tests until MISS_LIMIT consecutive misses.
"""
import json
import os
import sys
from playwright.sync_api import sync_playwright

BASE = "https://li3.di.uminho.pt/test/{}"
OUT = "li3_dump.json"
INITIAL_MAX = 128   # full sweep range used on the first run
MISS_LIMIT = 3      # stop probing upward after this many consecutive missing tests
REFRESH_LAST = 2    # re-fetch the newest N completed tests (they may still change)


def load_existing():
    if not os.path.exists(OUT):
        return {}
    with open(OUT, encoding="utf-8") as f:
        return {int(k): v for k, v in json.load(f).items()}


def is_done(rec):
    return rec is not None and "error" not in rec and bool(rec.get("text"))


def fetch(page, n):
    """Returns (status, record). status: 'ok' | 'error' | 'missing'."""
    captured = []

    def on_response(resp):
        try:
            if "json" in (resp.headers.get("content-type") or ""):
                captured.append({"url": resp.url, "data": resp.json()})
        except Exception:
            pass

    page.on("response", on_response)
    try:
        r = page.goto(BASE.format(n), wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(1000)
        if r is None or r.status >= 400:
            return "missing", None
        return "ok", {"text": page.inner_text("body"), "json": list(captured)}
    except Exception as e:
        return "error", {"error": str(e)}
    finally:
        page.remove_listener("response", on_response)


existing = load_existing()
out = dict(existing)

done = {n for n, rec in existing.items() if is_done(rec)}
completed = sorted(done)
refresh = set(completed[-REFRESH_LAST:]) if REFRESH_LAST else set()

todo = [n for n in range(1, INITIAL_MAX + 1) if n not in done or n in refresh]
print(f"Already have {len(done)} tests; fetching {len(todo)} in the sweep.")

errors = 0
attempted = 0

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    def scrape(n):
        global errors, attempted
        attempted += 1
        status, rec = fetch(page, n)
        if status == "missing":
            out.pop(n, None)
        else:
            out[n] = rec
            if status == "error":
                errors += 1
        print(f"/test/{n}: {status}")
        return status

    for n in todo:
        scrape(n)

    # Probe for tests beyond the known range.
    n = max(INITIAL_MAX, max(out, default=0)) + 1
    misses = 0
    while misses < MISS_LIMIT:
        status = scrape(n)
        misses = misses + 1 if status == "missing" else 0
        n += 1

    browser.close()

# Most fetches failing usually means the site is down or blocked: keep the old dump.
if attempted and errors > attempted // 2:
    print("Too many errors; not overwriting the existing dump.")
    sys.exit(1)

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(dict(sorted(out.items())), f, ensure_ascii=False, indent=2)
print(f"Saved {OUT} with {len(out)} tests.")
"""Incremental headless scraper for GitHub Actions.

- Sweeps tests 1..INITIAL_MAX, skipping tests already in li3_dump.json
  (failed ones and the newest REFRESH_LAST tests are fetched again).
- Then keeps probing upward until a test is missing: HTTP 404/4xx/5xx,
  a blank page, a "not found" page, or a request failure.
"""
import json
import os
import re
import sys
from playwright.sync_api import sync_playwright

BASE = "https://li3.di.uminho.pt/test/{}"
OUT = "li3_dump.json"
INITIAL_MAX = 128   # sweep range (tests 1..128 are always checked)
REFRESH_LAST = 5    # re-fetch the newest N completed tests on every run
MAX_TEST = 5000     # safety cap for the upward probe
NOT_FOUND = re.compile(r"\b(404|not found|não encontrad[ao])\b", re.I)


def load_existing():
    if not os.path.exists(OUT):
        return {}
    with open(OUT, encoding="utf-8") as f:
        return {int(k): v for k, v in json.load(f).items()}


def is_done(rec):
    return rec is not None and "error" not in rec and bool(rec.get("text", "").strip())


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
        text = page.inner_text("body").strip()
        if not text or (len(text) < 300 and NOT_FOUND.search(text)):
            return "missing", None
        return "ok", {"text": text, "json": list(captured)}
    except Exception as e:
        return "error", {"error": str(e)}
    finally:
        page.remove_listener("response", on_response)


existing = load_existing()
out = dict(existing)

done = {n for n, rec in existing.items() if is_done(rec)}
refresh = set(sorted(done)[-REFRESH_LAST:]) if REFRESH_LAST else set()
todo = [n for n in range(1, INITIAL_MAX + 1) if n not in done or n in refresh]
print(f"Have {len(done)} good tests; sweeping {len(todo)} of 1..{INITIAL_MAX}.")

errors = 0
attempted = 0

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    def scrape(n):
        """Returns 'ok', 'error' or 'missing'. Keeps old data when a test is missing."""
        global errors, attempted
        attempted += 1
        status, rec = fetch(page, n)
        if status == "missing":
            print(f"/test/{n}: missing")
            return status
        out[n] = rec
        if status == "error":
            errors += 1
        print(f"/test/{n}: {status}")
        return status

    for n in todo:
        scrape(n)

    # Probe upward until the first missing (or failed) test.
    n = max(INITIAL_MAX, max(out, default=0)) + 1
    while n <= MAX_TEST:
        if scrape(n) != "ok":
            break
        n += 1
    print(f"Probe stopped at /test/{n}.")

    browser.close()

# If most requests failed, the site is probably down or blocking us: keep the old dump.
if attempted and errors > attempted // 2:
    print("Too many errors; not overwriting the existing dump.")
    sys.exit(1)

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(dict(sorted(out.items())), f, ensure_ascii=False, indent=2)
print(f"Saved {OUT} with {len(out)} tests.")
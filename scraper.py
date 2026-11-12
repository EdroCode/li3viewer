import json
from playwright.sync_api import sync_playwright

BASE = "https://li3.di.uminho.pt/test/{}"
RANGE = range(1, 129)  # 0..129

out = {}

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context("./li3_profile", headless=False)
    page = ctx.new_page()

    page.goto(BASE.format(0))
    input("Faz login (se for preciso) e confirma que ves os resultados. Depois carrega ENTER... ")

    for n in RANGE:
        captured = []

        def on_response(resp):
            try:
                if "json" in (resp.headers.get("content-type") or ""):
                    captured.append({"url": resp.url, "data": resp.json()})
            except Exception:
                pass

        page.on("response", on_response)
        try:
            page.goto(BASE.format(n), wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(1000)
            out[n] = {
                "text": page.inner_text("body"),
                "json": captured,
            }
            print(f"/test/{n} ok ({len(captured)} respostas JSON)")
        except Exception as e:
            out[n] = {"error": str(e)}
            print(f"/test/{n} ERRO: {e}")
        finally:
            page.remove_listener("response", on_response)

    ctx.close()

with open("li3_dump.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

print("Guardado em li3_dump.json")
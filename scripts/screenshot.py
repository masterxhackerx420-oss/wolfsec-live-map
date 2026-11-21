"""Dev helper: headless screenshots of the running app (needs `pip install playwright`).

python scripts/screenshot.py http://localhost:8765 screenshots/all.png ["?layers=..."]
"""
import sys
from playwright.sync_api import sync_playwright

base, out = sys.argv[1], sys.argv[2]
query = sys.argv[3] if len(sys.argv) > 3 else ""
exe = sys.argv[4] if len(sys.argv) > 4 else None
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
    pg = b.new_page(viewport={"width": 1600, "height": 900})
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type in ("error", "warning") else None)
    pg.on("pageerror", lambda e: errs.append("PAGEERROR " + str(e)))
    pg.goto(base + "/" + query, wait_until="networkidle", timeout=90000)
    pg.wait_for_function("document.querySelectorAll('#layer-list .st.load').length === 0", timeout=90000)
    pg.wait_for_timeout(3500)
    print(pg.eval_on_selector_all("#layer-list li", "els => els.map(e => e.innerText.replace(/\\s+/g,' '))"))
    print("feed:", pg.inner_text("#feed-count"), "| badge:", pg.inner_text("#mode-badge"))
    if "--click" in sys.argv:  # open the newest feed item's popup
        pg.click("#feed-list li")
        pg.wait_for_timeout(4000)
    pg.screenshot(path=out)
    for e in errs:
        print("console:", e[:200])
    b.close()

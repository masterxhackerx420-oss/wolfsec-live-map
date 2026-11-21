"""Write every layer to docs/data/<layer>.json for the static (GitHub Pages) site.

Used by .github/workflows/update-data.yml every ~10 minutes, and can be run
locally:  python scripts/update_data.py [layer ...]
Exit code is non-zero only if *every* requested layer failed.
"""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from livemap.registry import LAYERS, ROOT, Hub  # noqa: E402

OUT = ROOT / "docs" / "data"


def main(argv):
    wanted = argv or list(LAYERS)
    hub = Hub()
    OUT.mkdir(parents=True, exist_ok=True)
    index, failed = {}, 0
    prev_index = json.loads((OUT / "index.json").read_text()) if (OUT / "index.json").exists() else {}
    # GDELT is shared by conflict/news/cyber: prime it once before fanning out
    if {"conflict", "news", "cyber"} & set(wanted):
        try:
            hub._gdelt()
        except Exception as e:
            print("GDELT prime failed:", e)
    with ThreadPoolExecutor(4) as ex:
        results = dict(zip(wanted, ex.map(hub.get, wanted)))
    for k in wanted:
        d = results[k]
        m = d["metadata"]
        path = OUT / f"{k}.json"
        if m["error"] and path.exists():
            # keep last good real data, flag it stale with the error
            old = json.loads(path.read_text())
            old["metadata"].update(error=m["error"], stale=True)
            d, m = old, old["metadata"]
        failed += bool(m["error"])
        path.write_text(json.dumps(d, separators=(",", ":"), ensure_ascii=False))
        index[k] = {"count": m["count"], "updated": m["updated"], "error": m["error"], "stale": m.get("stale")}
        print(f"{k:12s} {m['count']:5d}  {'ERROR ' + m['error'] if m['error'] else 'ok'}  ({path.stat().st_size // 1024} KB)")
    prev_index.update(index)
    prev_index["_generated"] = int(time.time() * 1000)
    (OUT / "index.json").write_text(json.dumps(prev_index, indent=1))
    return 1 if failed == len(wanted) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

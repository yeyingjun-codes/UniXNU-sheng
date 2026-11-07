from pathlib import Path
import json
import sys

changed = 0
for name in sys.argv[1:]:
    path = Path(name)
    if not path.exists():
        continue
    abi = json.loads(path.read_text())
    if abi["display"]["default_surface"] not in (7000, 7008):
        raise SystemExit(f"unexpected default_surface in {path}")
    # The original 24A446 create_default_fb_surface stores x0 at +0x1b60
    # (7008); its stop path reads and releases that same field.
    abi["display"]["default_surface"] = 7008
    path.write_text(json.dumps(abi, indent=2) + "\n")
    changed += 1
    print(f"corrected default_surface=7008 in {path}")
if not changed:
    raise SystemExit("no active ABI fixture found")

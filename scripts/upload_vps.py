# -*- coding: utf-8 -*-
import subprocess
import sys
from pathlib import Path

def main():
    root = Path(__file__).resolve().parent.parent
    dist_dir = root / "dist"
    if not dist_dir.exists():
        print(f"Dist directory not found: {dist_dir}")
        return 1

    items = [
        str(p) for p in dist_dir.glob("*")
        if not p.name.startswith("._") and p.name != ".DS_Store"
    ]
    if not items:
        print("No items to upload.")
        return 0

    cmd = ["scp", "-P", "22", "-r"] + items + ["root@217.142.149.2:/opt/1panel/www/tender_site/"]
    print("Executing:", " ".join(cmd))
    res = subprocess.run(cmd, capture_output=True, text=True)
    print("Return code:", res.returncode)
    if res.stdout:
        print(res.stdout.strip())
    if res.stderr:
        print(res.stderr.strip())
    return res.returncode

if __name__ == "__main__":
    sys.exit(main())

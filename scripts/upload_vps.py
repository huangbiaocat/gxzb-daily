# -*- coding: utf-8 -*-
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import config

def main():
    dist = Path(config.SITE_DIR)
    
    host = getattr(config, "VPS_HOST", "217.142.149.2")
    port = str(getattr(config, "VPS_PORT", "22"))
    user = getattr(config, "VPS_USER", "root")
    remote_path = getattr(config, "VPS_PATH", "/opt/1panel/www/tender_site/")
    if not remote_path.endswith("/"):
        remote_path += "/"
    
    remote_target = f"{user}@{host}:{remote_path}"
    
    items = []
    for name in ["index.html", "archive.html", "search.html"]:
        p = dist / name
        if p.exists():
            items.append(str(p))
            
    for p in dist.glob("20*.html"):
        items.append(str(p))
        
    for p in dist.glob("*.zip"):
        items.append(str(p))

    assets = dist / "assets"
    if assets.exists():
        items.append(str(assets))

    if not items:
        print("[VPS] dist 目录下未发现需要上传的 HTML/静态文件")
        return 0

    print(f"[VPS] 准备同步 {len(items)} 个文件/目录 -> {remote_target} (端口: {port})")
    cmd = ["scp", "-P", port, "-r"] + items + [remote_target]
    
    rc = subprocess.run(cmd).returncode
    if rc == 0:
        print("[VPS] 恭喜！同步上传 VPS 成功")
    else:
        print(f"[VPS] 上传失败，退出码: {rc}")
    return rc

if __name__ == "__main__":
    sys.exit(main())

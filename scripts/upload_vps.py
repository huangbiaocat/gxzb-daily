# -*- coding: utf-8 -*-
import shutil
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
    
    # 1. 优先尝试 rsync 增量同步（支持 --delete，自动清理远端已删除的历史页面）
    rsync_bin = shutil.which("rsync")
    if rsync_bin:
        ssh_opt = f"ssh -p {port} -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=no"
        if sys.platform != "win32":
            ssh_opt += " -o ControlMaster=auto -o ControlPath=/tmp/ssh-%r@%h:%p -o ControlPersist=10m"
        cmd = [
            rsync_bin,
            "-avz",
            "-e", ssh_opt,
            "--delete",
            "--exclude=logs/",
            "--exclude=*.zip.tmp",
            f"{str(dist)}/",
            remote_target
        ]
        print(f"[VPS] 尝试通过 rsync 同步 {dist}/ -> {remote_target} (自动删除远端冗余页面)")
        res = subprocess.run(cmd)
        if res.returncode == 0:
            print("[VPS] 恭喜！rsync 同步上传 VPS 成功（远端站点已与本地完全同步）")
            return 0
        print(f"[VPS] rsync 执行失败 (退出码: {res.returncode})，降级使用 scp 上传...")

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
    cmd = ["scp", "-P", port, "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=no"]
    key_path = getattr(config, "VPS_KEY_PATH", "").strip()
    if not key_path:
        local_key1 = REPO / "data" / "id_rsa"
        local_key2 = REPO / "data" / "vps_key.pem"
        if local_key1.is_file():
            key_path = str(local_key1)
        elif local_key2.is_file():
            key_path = str(local_key2)
    if key_path and Path(key_path).expanduser().is_file():
        cmd.extend(["-i", str(Path(key_path).expanduser().resolve())])
    if sys.platform != "win32":
        cmd.extend(["-o", "ControlMaster=auto", "-o", "ControlPath=/tmp/ssh-%r@%h:%p", "-o", "ControlPersist=10m"])
    cmd.extend(["-r"] + items + [remote_target])
    
    rc = subprocess.run(cmd).returncode
    if rc == 0:
        print("[VPS] 恭喜！同步上传 VPS 成功")
    else:
        print(f"[VPS] 上传失败，退出码: {rc}")
    return rc

if __name__ == "__main__":
    sys.exit(main())

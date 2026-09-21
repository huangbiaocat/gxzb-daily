# -*- coding: utf-8 -*-
import os
import sys
import zipfile
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import config

FILES_TO_PACK = [
    "config.py",
    "desktop_app.py",
    "run_desktop.bat",
    "run_daily.py",
    "scripts/__init__.py",
    "scripts/collect.py",
    "scripts/collect_cz_ygcg.py",
    "scripts/build_daily_page.py",
    "scripts/build_archive_page.py",
    "scripts/build_exe.py",
    "scripts/build_exe.bat",
    "scripts/diff_missing.py",
    "scripts/fetcher.py",
    "scripts/fetch_detail.py",
    "scripts/extract.py",
    "scripts/store.py",
    "scripts/logstore.py",
    "scripts/run_task.bat",
    "scripts/run_yesterday_final.py",
    "scripts/run_yesterday_final.bat",
    "scripts/ztb_task.xml",
    "scripts/ztb_yesterday_task.xml",
    "scripts/install_tasks.bat",
    "scripts/upload_vps.py",
    "scripts/notify_wechat.py",
    "scripts/sync_from_server.bat",
    "scripts/sync_from_server.ps1",
    "scripts/sync_and_run.bat",
    "scripts/update_and_run_desktop.bat",
    "scripts/create_desktop_shortcut.bat",
    "templates/index-sample.html",
    "templates/index-preview.html",
    "manager/server.py",
    "manager/static/index.html",
    "extractors/__init__.py",
    "extractors/normalize.py",
    "extractors/rules.py",
    "scripts/reapply_rules.py",
    "scripts/batch_scan.py",
    "scripts/upgrade_app.py",
]

def main():
    dist_dir = Path(config.SITE_DIR)
    dist_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dist_dir / "update_i5.zip"
    
    print(f"[Pack] 正在打包核心代码与静态控制台 -> {zip_path} ...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in FILES_TO_PACK:
            fp = REPO / rel
            if fp.exists():
                data = fp.read_bytes()
                if fp.suffix.lower() in [".bat", ".cmd", ".ps1"]:
                    data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
                zf.writestr(rel, data)
                print(f"  + 添加: {rel}")
            else:
                print(f"  ! 跳过不存在的文件: {rel}")
    
    print(f"[Pack] 打包完成，大小: {zip_path.stat().st_size / 1024:.1f} KB")
    
    # 同步上传到 VPS
    host = getattr(config, "VPS_HOST", "217.142.149.2")
    port = str(getattr(config, "VPS_PORT", "22"))
    user = getattr(config, "VPS_USER", "root")
    remote_path = getattr(config, "VPS_PATH", "/opt/1panel/www/tender_site/")
    if not remote_path.endswith("/"):
        remote_path += "/"
    
    print(f"[Upload] 上传 {zip_path.name} 及 dist 页面到 VPS ({host}:{remote_path}) ...")
    cmd = ["scp", "-P", port, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", "-o", "ControlMaster=auto", "-o", "ControlPath=/tmp/ssh-%r@%h:%p", "-o", "ControlPersist=10m", str(zip_path), f"{user}@{host}:{remote_path}"]
    res = subprocess.run(cmd)
    if res.returncode == 0:
        print("  -> update_i5.zip 上传 VPS 成功！")
    else:
        print(f"  -> 上传失败，退出码: {res.returncode}")
        return res.returncode
        
    # 同时也把最新的 html 页面传上去
    htmls = list(dist_dir.glob("*.html"))
    if htmls:
        cmd_html = ["scp", "-P", port, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", "-o", "ControlMaster=auto", "-o", "ControlPath=/tmp/ssh-%r@%h:%p", "-o", "ControlPersist=10m"] + [str(h) for h in htmls] + [f"{user}@{host}:{remote_path}"]
        res_html = subprocess.run(cmd_html)
        if res_html.returncode == 0:
            print(f"  -> {len(htmls)} 个 HTML 页面上传 VPS 成功！")
            
    return 0

if __name__ == "__main__":
    sys.exit(main())

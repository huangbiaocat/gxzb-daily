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
    "run_task.bat",
    "run_desktop.bat",
    "run_daily.py",
    "scripts/__init__.py",
    "scripts/collect.py",
    "scripts/collect_cz_ygcg.py",
    "scripts/build_daily_page.py",
    "scripts/build_archive_page.py",
    "scripts/build_search_index.py",
    "scripts/build_search_page.py",
    "scripts/build_exe.py",
    "scripts/build_exe.bat",
    "scripts/overtime_helper.py",
    "scripts/delayed_helper.py",
    "scripts/unmark_delayed.py",
    "scripts/scan_record_db.py",
    "scripts/backscan_delayed.py",
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
    "scripts/tunnel_guardian.py",
    "scripts/ensure_tunnel.bat",
    "scripts/sync_from_server.bat",
    "scripts/sync_from_server.ps1",
    "scripts/sync_and_run.bat",
    "scripts/update_and_run_desktop.bat",
    "scripts/create_desktop_shortcut.bat",
    "templates/index-sample.html",
    "templates/index-preview.html",
    "version.json",
    "manager/server.py",
    "manager/static/index.html",
    "extractors/__init__.py",
    "extractors/normalize.py",
    "extractors/rules.py",
    "scripts/reapply_rules.py",
    "scripts/batch_scan.py",
    "scripts/upgrade_app.py",
    "config/focus_rules_master.json",
]

def main():
    # 自动生成版本与提交元数据 version.json
    import json
    commit = ""
    date = ""
    message = ""
    commits = []
    try:
        commit = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=str(REPO), capture_output=True, text=True).stdout.strip()
        date = subprocess.run(['git', 'log', '-1', '--format=%cd', '--date=short'], cwd=str(REPO), capture_output=True, text=True).stdout.strip()
        message = subprocess.run(['git', 'log', '-1', '--format=%s'], cwd=str(REPO), capture_output=True, text=True).stdout.strip()
        log_res = subprocess.run(['git', 'log', '-6', '--pretty=format:%h\t%an\t%ad\t%s', '--date=short'], cwd=str(REPO), capture_output=True, text=True)
        if log_res.returncode == 0 and log_res.stdout.strip():
            for line in log_res.stdout.strip().splitlines():
                parts = line.split('\t')
                if len(parts) >= 4:
                    commits.append({'commit': parts[0], 'author': parts[1], 'date': parts[2], 'message': parts[3]})
    except Exception as e:
        print(f"[Warn] 读取 Git 版本信息失败: {e}")

    v_info = {
        "version": getattr(config, "APP_VERSION", "v0.3.6"),
        "commit": commit,
        "date": date,
        "message": message,
        "commits": commits
    }
    v_file = REPO / "version.json"
    with open(v_file, "w", encoding="utf-8") as f:
        json.dump(v_info, f, indent=2, ensure_ascii=False)
    print(f"[Version] 已生成构建元数据: {commit} ({date}) -> {v_file}")

    dist_dir = Path(config.SITE_DIR)
    dist_dir.mkdir(parents=True, exist_ok=True)
    dist_v_file = dist_dir / "version.json"
    dist_v_file.write_text(v_file.read_text(encoding="utf-8"), encoding="utf-8")
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
    
    print(f"[Upload] 上传 {zip_path.name}、version.json 及 dist 页面到 VPS ({host}:{remote_path}) ...")
    cmd = ["scp", "-P", port, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", "-o", "ControlMaster=auto", "-o", "ControlPath=/tmp/ssh-%r@%h:%p", "-o", "ControlPersist=10m", str(zip_path), str(dist_v_file), f"{user}@{host}:{remote_path}"]
    res = subprocess.run(cmd)
    if res.returncode == 0:
        print("  -> update_i5.zip 与 version.json 上传 VPS 成功！")
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

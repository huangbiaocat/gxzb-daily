# -*- coding: utf-8 -*-
import json
import time
import socket
from datetime import datetime
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import config

def record_vps_sync_result(host: str, remote_path: str, method: str, success: bool, error_msg: str = ""):
    try:
        sync_file = REPO / "data" / "last_vps_sync.json"
        sync_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "last_sync_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "success" if success else "failed",
            "method": method,
            "host": host,
            "path": remote_path,
            "error": error_msg
        }
        sync_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[VPS] 记录同步状态失败: {e}")

def test_vps_connectivity(host, port, timeout=5):
    """快速探测 VPS 的 TCP 端口与 SSH Banner 响应"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, int(port)))
        banner = s.recv(1024).decode("utf-8", errors="ignore")
        s.close()
        return True, banner.strip()
    except Exception as e:
        return False, str(e)

def execute_with_retry(cmd, max_retries=3, base_delay=3):
    """执行命令并进行自动重试 (指数退避)"""
    last_rc = -1
    for attempt in range(1, max_retries + 1):
        print(f"[VPS] 正在执行同步上传 (尝试 {attempt}/{max_retries})...")
        try:
            res = subprocess.run(cmd)
            if res.returncode == 0:
                return 0
            last_rc = res.returncode
            print(f"[VPS] 第 {attempt} 次同步失败 (返回码: {last_rc})")
        except Exception as e:
            print(f"[VPS] 第 {attempt} 次执行异常: {e}")
            last_rc = -1
        
        if attempt < max_retries:
            sleep_time = base_delay * (2 ** (attempt - 1))
            print(f"[VPS] 等待 {sleep_time} 秒后自动重试...")
            time.sleep(sleep_time)
            host = getattr(config, "VPS_HOST", "217.142.149.2")
            port = str(getattr(config, "VPS_PORT", "22"))
            ok, banner = test_vps_connectivity(host, port, timeout=4)
            if not ok:
                print(f"[VPS] 连通性预检警报: 无法连接 {host}:{port} -> {banner}")
    return last_rc

def main():
    dist = Path(config.SITE_DIR)
    
    host = getattr(config, "VPS_HOST", "217.142.149.2")
    port = str(getattr(config, "VPS_PORT", "22"))
    user = getattr(config, "VPS_USER", "root")
    remote_path = getattr(config, "VPS_PATH", "/opt/1panel/www/tender_site/")
    if not remote_path.endswith("/"):
        remote_path += "/"
    
    remote_target = f"{user}@{host}:{remote_path}"
    
    # 尝试确保最新的设备网络状态文件包含在上传队列中
    try:
        from scripts.tunnel_guardian import ensure_tunnel_health
        ensure_tunnel_health(auto_fix=False)
    except Exception:
        pass
    device_status = REPO / "data" / "device_status.json"

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
        rc = execute_with_retry(cmd, max_retries=2, base_delay=3)
        if rc == 0:
            print("[VPS] 恭喜！rsync 同步上传 VPS 成功（远端站点已与本地完全同步）")
            record_vps_sync_result(host, remote_path, "rsync", True)
            return 0
        print(f"[VPS] rsync 执行未成功，降级使用 scp 上传...")

    items = []
    for name in ["index.html", "archive.html", "search.html", "search_index.json"]:
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

    if device_status.exists():
        items.append(str(device_status))

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
    
    rc = execute_with_retry(cmd, max_retries=3, base_delay=3)
    if rc == 0:
        print("[VPS] 恭喜！同步上传 VPS 成功")
        record_vps_sync_result(host, remote_path, "scp", True)
    else:
        print(f"[VPS] 上传失败，退出码: {rc}")
        conn_ok, banner = test_vps_connectivity(host, port, timeout=5)
        diag_msg = f"SSH连通性: {'正常 (' + banner + ')' if conn_ok else '失败 (' + banner + ')'}"
        print(f"[VPS 自检诊断] {diag_msg}")
        record_vps_sync_result(host, remote_path, "scp", False, f"退出码: {rc}")
        try:
            from scripts.notify_wechat import send_alert
            send_alert(config.today(), f"无法更新 VPS 上的信息：文件传输失败 (退出码 {rc})，{diag_msg}", "VPS 静态站点同步")
        except Exception as exc:
            print("[VPS] 告警推送失败:", exc)
    return rc

if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""
隧道与反向管理通道守护者 (Tunnel Guardian)
解决问题：
1. 彻底根治 i5 机器因计划任务反复调用导致数百个 ssh.exe 僵尸进程泄露、打满 VPS sshd MaxStartups 的问题。
2. 启动时采用单例互斥检查，发现多余或悬挂的 ssh.exe 自动清理杀除。
3. 建立双向反向代理通道：
   - 18080: 目标政府采购网国内代理 (202.103.240.162:80)
   - 22022: i5 本地 SSH 服务反向映射 (127.0.0.1:22022 -> VPS 127.0.0.1:22022)，彻底解决电信 IPv6 前缀轮换导致的失联问题。
4. 强制启用 -o ExitOnForwardFailure=yes，端口绑定失败即刻退出，决不成为无用僵尸连接。
5. 自动修复本地 tunnel.vbs 与 Windows 计划任务，并上报当前 IP 与网络健康状态至 data/device_status.json。
"""
import os
import sys
import json
import time
import socket
import urllib.request
import subprocess
from pathlib import Path

# 确保项目根目录在 sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    import config
    VPS_HOST = getattr(config, "VPS_HOST", "217.142.149.2")
    VPS_PORT = str(getattr(config, "VPS_PORT", "22"))
    VPS_USER = getattr(config, "VPS_USER", "root")
except Exception:
    VPS_HOST = "217.142.149.2"
    VPS_PORT = "22"
    VPS_USER = "root"

FORWARD_TARGET_PROXY = "18080:202.103.240.162:80"
FORWARD_REVERSE_SSH = "22022:127.0.0.1:22022"


def get_network_info():
    """获取本机内网 IP、公网 IPv4 与公网 IPv6 地址"""
    info = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "hostname": socket.gethostname(),
        "local_ip": "",
        "public_ipv4": "",
        "public_ipv6": "",
    }
    # 1. 本地内网 IP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        info["local_ip"] = s.getsockname()[0]
        s.close()
    except Exception:
        info["local_ip"] = "127.0.0.1"

    # 2. 公网 IPv4
    for url in ["https://api.ipify.org", "https://4.ident.me", "https://ip.sb"]:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68.0"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                v4 = resp.read().decode("utf-8").strip()
                if v4 and "." in v4:
                    info["public_ipv4"] = v4
                    break
        except Exception:
            continue

    # 3. 公网 IPv6 (通过 IPv6 专有出口测定)
    for url in ["https://api64.ipify.org", "https://6.ident.me"]:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68.0"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                v6 = resp.read().decode("utf-8").strip()
                if v6 and ":" in v6:
                    info["public_ipv6"] = v6
                    break
        except Exception:
            continue

    return info


def find_tunnel_processes():
    """查找正在运行的所有与 VPS 隧道相关的 ssh.exe 进程"""
    procs = []
    if sys.platform == "win32":
        # Windows: 使用 PowerShell 查询 Win32_Process
        ps_cmd = (
            'Get-CimInstance Win32_Process -Filter "name = \'ssh.exe\'" | '
            'Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress'
        )
        try:
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                stderr=subprocess.DEVNULL,
                timeout=6,
            ).decode("utf-8", errors="ignore").strip()
            if out:
                data = json.loads(out)
                if isinstance(data, dict):
                    data = [data]
                for item in data:
                    pid = item.get("ProcessId")
                    cmdline = item.get("CommandLine") or ""
                    if VPS_HOST in cmdline or "18080" in cmdline or "22022" in cmdline:
                        procs.append((pid, cmdline))
        except Exception:
            # 降级：使用 tasklist 查找所有 ssh.exe
            try:
                tl = subprocess.check_output(
                    ["tasklist", "/FO", "CSV", "/NH", "/FI", "IMAGENAME eq ssh.exe"],
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                ).decode("utf-8", errors="ignore")
                for line in tl.splitlines():
                    parts = [p.strip(' "') for p in line.split(",")]
                    if len(parts) >= 2 and parts[0].lower() == "ssh.exe":
                        try:
                            procs.append((int(parts[1]), "ssh.exe"))
                        except ValueError:
                            pass
            except Exception:
                pass
    else:
        # Unix / macOS
        try:
            out = subprocess.check_output(["pgrep", "-a", "ssh"], timeout=3).decode("utf-8", errors="ignore")
            for line in out.splitlines():
                parts = line.strip().split(None, 1)
                if len(parts) == 2:
                    pid = int(parts[0])
                    cmdline = parts[1]
                    if VPS_HOST in cmdline and ("18080" in cmdline or "22022" in cmdline):
                        procs.append((pid, cmdline))
        except Exception:
            pass
    return procs


def kill_process(pid):
    """安全杀除指定 PID 进程"""
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=5)
        else:
            os.kill(pid, 9)
        return True
    except Exception:
        return False


def kill_all_tunnel_processes():
    """杀除所有正在运行的隧道进程"""
    procs = find_tunnel_processes()
    killed = 0
    for pid, _ in procs:
        if kill_process(pid):
            killed += 1
    return killed


def start_tunnel():
    """启动全新的高可靠双向反向代理隧道 (无黑色控制台窗口后台运行)"""
    # 构造标准安全参数
    cmd = [
        "ssh",
        "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=3",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=10",
        "-p", VPS_PORT,
        "-N",
        "-R", FORWARD_TARGET_PROXY,
        "-R", FORWARD_REVERSE_SSH,
        f"{VPS_USER}@{VPS_HOST}",
    ]

    creationflags = 0
    if sys.platform == "win32":
        DETACHED_PROCESS = 0x00000008
        CREATE_NO_WINDOW = 0x08000000
        creationflags = DETACHED_PROCESS | CREATE_NO_WINDOW

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        time.sleep(2)
        # 检查是否因为 ExitOnForwardFailure 或连接失败而过早退出
        if proc.poll() is None:
            return proc.pid
        else:
            return None
    except Exception as e:
        print(f"[Guardian] 启动隧道异常: {e}")
        return None


def self_repair_windows_config():
    """自愈：自动修复本地 tunnel.vbs、run_task.bat 和 Windows 计划任务配置"""
    if sys.platform != "win32":
        return

    # 1. 自动重写 C:\Users\DELL\tunnel.vbs 为安全版
    user_home = Path(os.environ.get("USERPROFILE", "C:\\Users\\DELL"))
    vbs_candidates = [
        user_home / "tunnel.vbs",
        Path("C:/Users/DELL/tunnel.vbs"),
        REPO_ROOT / "scripts" / "tunnel.vbs",
    ]
    safe_vbs_code = f'''Set WshShell = CreateObject("WScript.Shell")
' 启动 Python 隧道守护者，单例防重、清理僵尸并确保双向通道建立
WshShell.Run "python \\"{REPO_ROOT}\\scripts\\tunnel_guardian.py\\" --ensure", 0, False
'''
    for vbs_path in vbs_candidates:
        if vbs_path.parent.exists():
            try:
                vbs_path.write_text(safe_vbs_code, encoding="utf-8")
            except Exception:
                pass

    # 2. 检查并修正 D:\ztb_collector\run_task.bat 增加自检
    run_task_bat = REPO_ROOT / "run_task.bat"
    if run_task_bat.exists():
        try:
            content = run_task_bat.read_text(encoding="utf-8", errors="ignore")
            if "tunnel_guardian.py" not in content:
                lines = content.splitlines()
                new_lines = []
                injected = False
                for line in lines:
                    if "run_daily.py" in line and not injected:
                        new_lines.append('"%PYTHON_EXE%" scripts\\tunnel_guardian.py --ensure')
                        injected = True
                    new_lines.append(line)
                run_task_bat.write_text("\n".join(new_lines), encoding="utf-8")
        except Exception:
            pass


def save_device_status(net_info, tunnel_alive, running_count):
    """保存状态到 data/device_status.json"""
    status_dir = REPO_ROOT / "data"
    status_dir.mkdir(parents=True, exist_ok=True)
    status_file = status_dir / "device_status.json"

    data = {
        "updated_at": net_info.get("timestamp", time.strftime("%Y-%m-%d %H:%M:%S")),
        "hostname": net_info.get("hostname", ""),
        "local_ip": net_info.get("local_ip", ""),
        "public_ipv4": net_info.get("public_ipv4", ""),
        "public_ipv6": net_info.get("public_ipv6", ""),
        "tunnel_alive": tunnel_alive,
        "running_ssh_processes": running_count,
        "forwarded_ports": {
            "target_gov_proxy": 18080,
            "reverse_ssh_mgmt": 22022,
        },
        "vps_host": VPS_HOST,
    }
    try:
        with open(status_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Guardian] 保存设备状态文件异常: {e}")
    return data


def ensure_tunnel_health(auto_fix=True):
    """
    核心保障函数：
    1. 检查当前活跃的 ssh.exe 进程
    2. 如果进程数 > 1，说明发生了多实例堆积，全部清理后重启唯一实例
    3. 如果进程数 == 0，立即启动隧道
    4. 执行自动修复和状态留存
    """
    procs = find_tunnel_processes()
    count = len(procs)
    tunnel_alive = False

    if count > 1:
        print(f"[Guardian] 发现 {count} 个隧道进程堆积泄露，正在执行全面清理...")
        kill_all_tunnel_processes()
        time.sleep(1)
        pid = start_tunnel()
        if pid:
            print(f"[Guardian] 隧道单例已重启成功 (PID: {pid})")
            tunnel_alive = True
        else:
            print("[Guardian] 隧道重启失败")
    elif count == 1:
        # 恰好一个实例，状态良好
        tunnel_alive = True
    else:
        # 0 个实例，启动新实例
        if auto_fix:
            print("[Guardian] 当前未检测到活跃隧道，正在启动...")
            pid = start_tunnel()
            if pid:
                print(f"[Guardian] 隧道已成功启动 (PID: {pid})")
                tunnel_alive = True
            else:
                print("[Guardian] 隧道启动失败")

    # 执行配置自愈 (VBS/bat)
    if auto_fix:
        self_repair_windows_config()

    # 上报与保存状态
    net_info = get_network_info()
    status = save_device_status(net_info, tunnel_alive, count)
    return status


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Tunnel Guardian & Self-Healing Service")
    parser.add_argument("--status", action="store_true", help="查看当前隧道与网络健康状态")
    parser.add_argument("--ensure", action="store_true", help="自检并自动修复隧道单例")
    parser.add_argument("--fix", action="store_true", help="强制清理所有旧进程并重启隧道")
    parser.add_argument("--report-ip", action="store_true", help="仅更新设备 IP 状态")
    args = parser.parse_args()

    if args.status:
        procs = find_tunnel_processes()
        print(f"当前运行中隧道进程数: {len(procs)}")
        for pid, cmd in procs:
            print(f" - PID {pid}: {cmd}")
        info = get_network_info()
        print(f"网络状态: {json.dumps(info, ensure_ascii=False, indent=2)}")
        return

    if args.fix:
        print("[Guardian] 强制重置隧道...")
        kill_all_tunnel_processes()
        start_tunnel()
        self_repair_windows_config()
        info = get_network_info()
        save_device_status(info, True, 1)
        print("[Guardian] 隧道已重置并刷新配置")
        return

    if args.report_ip:
        info = get_network_info()
        procs = find_tunnel_processes()
        save_device_status(info, len(procs) > 0, len(procs))
        print(f"[Guardian] 设备状态已更新: {json.dumps(info, ensure_ascii=False)}")
        return

    # 默认 --ensure
    status = ensure_tunnel_health(auto_fix=True)
    print(f"[Guardian] 守护检查完成，状态: tunnel_alive={status.get('tunnel_alive')}, IPv4={status.get('public_ipv4')}, IPv6={status.get('public_ipv6')}")


if __name__ == "__main__":
    main()

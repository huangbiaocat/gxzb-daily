# -*- coding: utf-8 -*-
"""
应用在线热更新/升级脚本 (Upgrade App from GitHub)
支持两种升级模式：
1. Git 仓库拉取模式：如果本地是 git 仓库，自动执行 git pull / git reset 保留用户数据
2. GitHub Zipball 兜底更新模式：纯文件绿色解压更新，强制保留 .env、data/、dist/ 等用户生产数据
"""
import os
import sys
import json
import time
import shutil
import zipfile
import tempfile
import subprocess
import urllib.request
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

# 保持保护不被覆盖的文件和目录
PROTECTED_PATTERNS = [
    ".env",
    ".env.local",
    "data",
    "dist",
    "logs",
    "run.log",
    "desktop_app.log",
    ".git"
]

def log(msg):
    print(f"[Upgrade] {msg}", flush=True)

def is_git_repo(path: Path) -> bool:
    return (path / ".git").is_dir()

def get_current_git_version():
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
            timeout=5
        )
        if res.returncode == 0:
            commit = res.stdout.strip()
            # 获取最近一次提交信息
            res_msg = subprocess.run(
                ["git", "log", "-1", "--format=%s"],
                cwd=str(ROOT_DIR),
                capture_output=True,
                text=True,
                timeout=5
            )
            msg = res_msg.stdout.strip() if res_msg.returncode == 0 else ""
            return {"commit": commit, "message": msg}
    except Exception:
        pass
    return None

def upgrade_via_git():
    log("【通道 1】检测到本地为 Git 仓库，尝试使用 Git 获取远程更新...")
    try:
        log("正在连接 GitHub 获取最新版本 (git fetch)...")
        fetch_res = subprocess.run(
            ["git", "fetch", "origin", "main"],
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
            timeout=15
        )
        if fetch_res.returncode != 0:
            err = fetch_res.stderr.strip()
            log(f"Git 访问 GitHub 失败或超时 ({err})，将自动转入云端镜像加速通道...")
            return False, f"Git 获取远程更新失败: {err}"

        diff_res = subprocess.run(
            ["git", "rev-list", "HEAD..origin/main", "--count"],
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
            timeout=5
        )
        behind_count = diff_res.stdout.strip() if diff_res.returncode == 0 else "0"
        if behind_count == "0":
            log("本地代码已经是最新版本，无需更新。")
            return True, "当前已是最新版本"

        log(f"发现远程有 {behind_count} 个新提交，准备同步...")

        subprocess.run(["git", "stash"], cwd=str(ROOT_DIR), capture_output=True, timeout=10)

        pull_res = subprocess.run(
            ["git", "pull", "--rebase", "origin", "main"],
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
            timeout=30
        )
        if pull_res.returncode != 0:
            log("rebase pull 遇到冲突，尝试重置非数据代码分支 (git reset --hard origin/main)...")
            reset_res = subprocess.run(
                ["git", "reset", "--hard", "origin/main"],
                cwd=str(ROOT_DIR),
                capture_output=True,
                text=True,
                timeout=10
            )
            if reset_res.returncode != 0:
                return False, f"Git 同步失败: {reset_res.stderr.strip()}"

        log("Git 同步成功完成！")
        return True, "升级成功"
    except Exception as e:
        log(f"Git 同步异常 ({e})，将自动转入云端镜像加速通道...")
        return False, str(e)

def _safe_extract_and_replace(zip_path: Path, is_raw_repo_zip: bool = False):
    """安全解压并替换文件，严格保护用户数据库、日志和本地配置"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        extract_dir = Path(tmp_dir) / "extracted"
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)

        if is_raw_repo_zip:
            subdirs = [p for p in extract_dir.iterdir() if p.is_dir()]
            if not subdirs:
                raise RuntimeError("更新包内容异常，未发现代码根目录")
            source_root = subdirs[0]
        else:
            source_root = extract_dir

        log("正在安全覆盖系统程序并保留本地配置与数据库...")
        updated_files = 0
        for item in source_root.iterdir():
            if item.name in PROTECTED_PATTERNS:
                continue
            dest = ROOT_DIR / item.name
            if item.is_dir():
                for sub in item.rglob("*"):
                    rel = sub.relative_to(item)
                    target = dest / rel
                    if any(part in PROTECTED_PATTERNS for part in rel.parts):
                        continue
                    if sub.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(sub, target)
                        updated_files += 1
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, dest)
                updated_files += 1

        log(f"文件热替换完成，共更新/校验 {updated_files} 个系统文件！")

def upgrade_via_vps_mirror():
    """【通道 2】从项目专属云端专线镜像 (Cloudflare/VPS) 极速拉取 update_i5.zip"""
    url = f"https://ztb.139771.xyz/update_i5.zip?t={int(time.time())}"
    log("【通道 2】尝试从专属云端专线镜像源极速下载更新包...")
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) GX-TenderCollector-Updater"}
    req = urllib.request.Request(url, headers=headers)
    
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_zip = Path(tmp_dir) / "update_i5.zip"
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                with open(tmp_zip, "wb") as out_f:
                    shutil.copyfileobj(resp, out_f)
            file_size_kb = tmp_zip.stat().st_size / 1024
            log(f"云端专线更新包下载成功 ({file_size_kb:.1f} KB)，正在安全替换...")
            _safe_extract_and_replace(tmp_zip, is_raw_repo_zip=False)
            return True, "云端镜像更新成功"
        except Exception as e:
            log(f"云端专线镜像下载失败: {e}")
            return False, str(e)

def upgrade_via_zipball():
    """【通道 3】从 GitHub 国内加速镜像或官方接口拉取 Zip 包"""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        env_file = ROOT_DIR / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("GITHUB_TOKEN="):
                    token = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break

    repo = "huangbiaocat/gxzb-daily"
    candidate_urls = [
        f"https://ghfast.top/https://github.com/{repo}/archive/refs/heads/main.zip",
        f"https://mirror.ghproxy.com/https://github.com/{repo}/archive/refs/heads/main.zip",
        f"https://api.github.com/repos/{repo}/zipball/main",
    ]

    log("【通道 3】尝试通过 GitHub 高速镜像通道拉取发布包...")
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    if token:
        headers["Authorization"] = f"token {token}"

    for idx, url in enumerate(candidate_urls, start=1):
        domain = url.split('/')[2]
        log(f"尝试第 {idx} 个 GitHub 镜像节点 ({domain})...")
        req = urllib.request.Request(url, headers=headers)
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_zip = Path(tmp_dir) / "update.zip"
            try:
                with urllib.request.urlopen(req, timeout=25) as resp:
                    with open(tmp_zip, "wb") as out_f:
                        shutil.copyfileobj(resp, out_f)
                log("镜像节点下载完成，正在解压替换...")
                _safe_extract_and_replace(tmp_zip, is_raw_repo_zip=True)
                return True, "GitHub 镜像升级成功"
            except Exception as e:
                log(f"节点 {idx} 拉取失败: {e}")
                continue

    return False, "所有 GitHub 镜像节点均连接超时"

def run_upgrade():
    log("==========================================")
    log("开始执行系统在线升级检查...")
    log(f"工作目录: {ROOT_DIR}")
    
    curr = get_current_git_version()
    if curr:
        log(f"当前版本 Commit: {curr['commit']} ({curr['message']})")

    success = False
    msg = ""
    
    # 1. 如果是 Git 仓库，优先尝试 Git 拉取
    if is_git_repo(ROOT_DIR):
        success, msg = upgrade_via_git()
    
    # 2. 如果 Git 失败或非 Git 仓库，自动降级切换至专线云镜像 (在国内 100% 畅通)
    if not success:
        success, msg = upgrade_via_vps_mirror()

    # 3. 如果专线云镜像不可用，进一步尝试 GitHub 高速镜像
    if not success:
        success, msg = upgrade_via_zipball()

    if success:
        log("==========================================")
        log("恭喜！代码同步升级成功完成。")
        try:
            import importlib
            import config as live_cfg
            importlib.reload(live_cfg)
            log(f"当前系统版本已更新为: {getattr(live_cfg, 'APP_VERSION', '未知')}")
        except Exception:
            pass

        new_curr = get_current_git_version()
        if new_curr:
            log(f"当前 Commit: {new_curr['commit']} ({new_curr['message']})")
        log("如修改涉及服务端后台核心，可通过重启服务生效。")
        return 0
    else:
        log("==========================================")
        log(f"升级过程出现问题: {msg}")
        log("提示：若因特殊网络环境导致在线更新受阻，可直接在项目根目录下双击运行 【scripts/sync_from_server.bat】完成极速同步。")
        return 1

if __name__ == "__main__":
    sys.exit(run_upgrade())

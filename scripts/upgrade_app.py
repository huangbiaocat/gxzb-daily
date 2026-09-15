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
    log("检测到本地为 Git 仓库，优先使用 Git 同步更新...")
    try:
        log("正在连接 GitHub 获取最新版本 (git fetch)...")
        fetch_res = subprocess.run(
            ["git", "fetch", "origin", "main"],
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
            timeout=30
        )
        if fetch_res.returncode != 0:
            err = fetch_res.stderr.strip()
            log(f"git fetch 失败: {err}")
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
        log(f"Git 更新异常: {e}")
        return False, str(e)

def upgrade_via_zipball():
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        env_file = ROOT_DIR / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("GITHUB_TOKEN="):
                    token = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break

    repo = "huangbiaocat/gxzb-daily"
    url = f"https://api.github.com/repos/{repo}/zipball/main"
    log("正在从 GitHub 官方接口拉取最新发布包...")
    
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    if token:
        headers["Authorization"] = f"token {token}"

    req = urllib.request.Request(url, headers=headers)
    
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_zip = Path(tmp_dir) / "update.zip"
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                with open(tmp_zip, "wb") as out_f:
                    shutil.copyfileobj(resp, out_f)
            log("更新包下载完成，正在解压校验...")
        except Exception as e:
            log(f"下载更新包失败: {e}")
            return False, f"下载更新包失败: {e}"

        extract_dir = Path(tmp_dir) / "extracted"
        with zipfile.ZipFile(tmp_zip, "r") as zf:
            zf.extractall(extract_dir)

        subdirs = [p for p in extract_dir.iterdir() if p.is_dir()]
        if not subdirs:
            return False, "更新包内容异常，未发现代码根目录"
        source_root = subdirs[0]

        log("正在安全覆盖程序文件并保留本地配置与数据库...")
        for item in source_root.iterdir():
            if item.name in PROTECTED_PATTERNS:
                continue
            dest = ROOT_DIR / item.name
            if item.is_dir():
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)

        log("文件热替换完成！")
        return True, "升级成功"

def run_upgrade():
    log("==========================================")
    log("开始执行系统在线升级检查...")
    log(f"工作目录: {ROOT_DIR}")
    
    curr = get_current_git_version()
    if curr:
        log(f"当前版本 Commit: {curr['commit']} ({curr['message']})")

    success = False
    msg = ""
    if is_git_repo(ROOT_DIR):
        success, msg = upgrade_via_git()
    
    if not success and not is_git_repo(ROOT_DIR):
        log("尝试 Zipball 兜底更新模式...")
        success, msg = upgrade_via_zipball()

    if success:
        log("==========================================")
        log("恭喜！代码同步升级成功完成。")
        new_curr = get_current_git_version()
        if new_curr:
            log(f"升级后版本 Commit: {new_curr['commit']} ({new_curr['message']})")
        log("如修改涉及服务端后台核心，可通过重启服务生效。")
        return 0
    else:
        log("==========================================")
        log(f"升级过程出现问题: {msg}")
        return 1

if __name__ == "__main__":
    sys.exit(run_upgrade())

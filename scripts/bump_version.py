#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动递增 config.py 中的 APP_VERSION (patch 版本 +1)，
然后 git add + git commit。

用法:
  python3 scripts/bump_version.py          # patch +1 (默认)
  python3 scripts/bump_version.py minor    # minor +1, patch 归零
  python3 scripts/bump_version.py major    # major +1, minor/patch 归零
  python3 scripts/bump_version.py --dry    # 只显示新版本，不写文件
"""
import re
import sys
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CONFIG_FILE = REPO / "config.py"
VERSION_RE = re.compile(r'^(APP_VERSION\s*=\s*["\'])v?(\d+)\.(\d+)\.(\d+)(["\'])', re.MULTILINE)


def read_version(text: str):
    m = VERSION_RE.search(text)
    if not m:
        raise SystemExit("[bump] config.py 中未找到 APP_VERSION 定义")
    return int(m.group(2)), int(m.group(3)), int(m.group(4)), m


def bump(major: int, minor: int, patch: int, level: str = "patch"):
    if level == "major":
        return major + 1, 0, 0
    if level == "minor":
        return major, minor + 1, 0
    return major, minor, patch + 1


def main():
    dry = "--dry" in sys.argv
    level = "patch"
    for a in sys.argv[1:]:
        if a in ("major", "minor", "patch"):
            level = a

    text = CONFIG_FILE.read_text(encoding="utf-8")
    major, minor, patch, m = read_version(text)
    old_ver = f"v{major}.{minor}.{patch}"
    new = bump(major, minor, patch, level)
    new_ver = f"v{new[0]}.{new[1]}.{new[2]}"

    if dry:
        print(f"{old_ver} -> {new_ver} (dry run)")
        return

    # 替换版本号
    quote = m.group(1)[-1]
    replacement = f'APP_VERSION = {quote}{new_ver}{quote}'
    new_text = VERSION_RE.sub(replacement, text, count=1)
    CONFIG_FILE.write_text(new_text, encoding="utf-8")
    print(f"[bump] {old_ver} -> {new_ver}")

    # git add + commit
    subprocess.run(["git", "add", str(CONFIG_FILE)], cwd=str(REPO), check=True)
    subprocess.run(
        ["git", "commit", "-m", f"chore(release): bump version {new_ver}"],
        cwd=str(REPO),
        check=True,
    )
    print(f"[bump] 已提交版本变更: {new_ver}")


if __name__ == "__main__":
    main()

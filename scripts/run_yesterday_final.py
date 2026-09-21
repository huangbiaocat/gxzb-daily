# -*- coding: utf-8 -*-
"""昨日标讯最终版采集与封存脚本。

功能：
    每天凌晨 00:10（或开机补跑）最后扫描一次前一天的全网标讯，
    执行去重合并入库、重新生成昨日明细页（附带全天终版封存徽标）、
    刷新归档日历/列表、上传至 VPS 云端，并发送昨日终版汇总推送。

用法：
    python scripts/run_yesterday_final.py               # 自动归档昨天
    python scripts/run_yesterday_final.py --force       # 强制重新扫描并封存
    python scripts/run_yesterday_final.py --date 2026-09-20  # 指定特定日期封存终版
"""

import argparse
from datetime import datetime, timedelta
from pathlib import Path
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
import config  # noqa: E402


def parse_args():
    parser = argparse.ArgumentParser(description="扫描昨日讯息并保存为全天最终版")
    parser.add_argument("--date", default=None, help="目标归档日期 YYYY-MM-DD（默认昨天）")
    parser.add_argument("--force", action="store_true", help="强制覆盖已封存的最终版")
    parser.add_argument("--no-push", action="store_true", help="跳过微信推送")
    parser.add_argument("--skip-archive", action="store_true", help="跳过归档页构建")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.date:
        target_date = args.date.strip()
    else:
        target_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    final_marker = config.STATE_DIR / f"final-{target_date}.json"
    if final_marker.exists() and not args.force:
        print(f"[*] 提示：日期 {target_date} 已于之前封存为最终版 ({final_marker})。")
        print("    若需重新扫描并覆盖封存，请加上 --force 参数。")
        return 0

    print(f"\n{'='*70}")
    print(f"[*] 开始执行标讯最终版扫描与封存流水线")
    print(f"[*] 目标日期 : {target_date}")
    print(f"[*] 启动时间 : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}\n")

    cmd = [
        sys.executable,
        "-u",
        str(REPO_ROOT / "run_daily.py"),
        "--date", target_date,
        "--final",
        "--force"
    ]
    if args.no_push:
        cmd.append("--no-push")
    if args.skip_archive:
        cmd.append("--skip-archive")

    res = subprocess.run(cmd, cwd=str(REPO_ROOT))
    if res.returncode == 0:
        print(f"\n[+] 目标日期 {target_date} 全天标讯最终版封存成功完成！")
    else:
        print(f"\n[!] 目标日期 {target_date} 全天标讯最终版执行退出，退出码: {res.returncode}")
    return res.returncode


if __name__ == "__main__":
    sys.exit(main())

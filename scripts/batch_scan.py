# -*- coding: utf-8 -*-
"""历史日期区间批量补扫与全流程归档工具。

支持按起止日期顺序执行：
1. 抓取每日公告数据（调用 scripts/collect.py 与 scripts/collect_cz_ygcg.py）
2. 构建并生成每日 HTML（调用 scripts/build_daily_page.py）
3. 刷新历史归档总索引（调用 scripts/build_archive_page.py）
4. 若开启 VPS 自动发布，全量同步至云端站点（调用 scripts/upload_vps.py）

用法:
    python scripts/batch_scan.py --start-date 2026-09-01 --end-date 2026-09-15
"""
import sys
# 控制台编码保护，彻底避免 Windows GBK 环境下 UnicodeEncodeError 中断批量调度
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import time
import argparse
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
import config


def parse_args():
    p = argparse.ArgumentParser(description="历史数据批量补扫调度工具")
    p.add_argument("--start-date", required=True, help="起始补扫日期 YYYY-MM-DD")
    p.add_argument("--end-date", required=True, help="截止补扫日期 YYYY-MM-DD")
    p.add_argument("--skip-upload", action="store_true", help="完成补扫后跳过自动上传 VPS")
    return p.parse_args()


def main():
    args = parse_args()
    py_exe = sys.executable

    try:
        d_start = datetime.strptime(args.start_date.strip(), "%Y-%m-%d").date()
        d_end = datetime.strptime(args.end_date.strip(), "%Y-%m-%d").date()
    except Exception as e:
        print(f"[错误] 日期格式不正确，必须为 YYYY-MM-DD 格式: {e}")
        sys.exit(1)

    if d_start > d_end:
        d_start, d_end = d_end, d_start

    curr = d_start
    target_dates = []
    while curr <= d_end:
        target_dates.append(curr.isoformat())
        curr += timedelta(days=1)

    print("=" * 68)
    print(f"[*] 启动历史日期批量补扫任务：共 {len(target_dates)} 天 ({target_dates[0]} 至 {target_dates[-1]})", flush=True)
    print("=" * 68)

    success_days = []
    failed_days = []

    for idx, day_str in enumerate(target_dates, 1):
        t_start = time.time()
        pct = int(idx / len(target_dates) * 100)
        print(f"\n" + "-" * 50, flush=True)
        print(f">>> [{idx}/{len(target_dates)} - 进度 {pct}%] 正在补扫执行日期: {day_str} ...", flush=True)
        try:
            # 1. 执行单日采集 (含公共资源交易中心与崇左阳光采购)
            print(f"    [步骤 1/3] 抓取当日公告数据 (公共资源 + 崇左阳光采购)...", flush=True)
            cmd_collect = [py_exe, "-u", str(ROOT_DIR / "scripts" / "collect.py"), "--date", day_str]
            res1 = subprocess.run(cmd_collect)
            if res1.returncode != 0:
                print(f"    [警告] {day_str} 采集脚本退出码异常: {res1.returncode}", flush=True)

            # 2. 数据合并入库 (reconcile)
            print(f"    [步骤 2/3] 合并数据入库 (reconcile)...", flush=True)
            from run_daily import reconcile, refresh_archive
            try:
                reconcile(day_str, "merge")
            except Exception as e_rec:
                print(f"    [警告] {day_str} 合并入库异常: {e_rec}", flush=True)

            # 3. 构建每日 HTML 页面（单日补扫跳过逐日上传 VPS，全量结束后统一发布）
            print(f"    [步骤 3/3] 构建每日日报 HTML 页面...", flush=True)
            cmd_build = [py_exe, "-u", str(ROOT_DIR / "scripts" / "build_daily_page.py"), "--date", day_str, "--no-vps"]
            res2 = subprocess.run(cmd_build)
            elapsed = time.time() - t_start
            if res2.returncode == 0:
                success_days.append(day_str)
                print(f"[OK] 日期 {day_str} 采集并构建完成！(耗时: {elapsed:.1f}s)", flush=True)
                try:
                    refresh_archive(day_str)
                except Exception:
                    pass
            else:
                failed_days.append(day_str)
                print(f"[FAIL] 日期 {day_str} 构建失败，返回码: {res2.returncode} (耗时: {elapsed:.1f}s)", flush=True)
        except Exception as e_day:
            failed_days.append(day_str)
            print(f"[FAIL] 日期 {day_str} 调度过程发生未捕获异常: {e_day}，继续处理下一日...", flush=True)

    # 4. 全部补扫完成后，全量重构归档总表（index.html）
    print("\n" + "=" * 68, flush=True)
    print("[*] 所有指定日期已完成扫描，正在刷新更新历史归档总索引 (index.html) ...", flush=True)
    subprocess.run([py_exe, "-u", str(ROOT_DIR / "scripts" / "build_archive_page.py")])

    # 5. 若配置了自动上传，统一同步云端
    if not args.skip_upload and getattr(config, "AUTO_UPLOAD_VPS", True):
        print("\n[*] 正在将全量补扫数据与网页同步发布到云端服务器 ...", flush=True)
        upload_script = ROOT_DIR / "scripts" / "upload_vps.py"
        if upload_script.exists():
            subprocess.run([py_exe, "-u", str(upload_script)])

    print("\n" + "=" * 68)
    print(f"[*] 批量补扫总结: 成功 {len(success_days)} 天，失败 {len(failed_days)} 天", flush=True)
    if failed_days:
        print(f"失败日期列表: {failed_days}", flush=True)
    print("=" * 68)


if __name__ == "__main__":
    main()

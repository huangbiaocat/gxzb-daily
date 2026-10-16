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
    p.add_argument("--start-date", "--start", dest="start_date", default="", help="起始补扫日期 YYYY-MM-DD")
    p.add_argument("--end-date", "--end", dest="end_date", default="", help="截止补扫日期 YYYY-MM-DD")
    p.add_argument("--dates", dest="dates", default="", help="指定的日期列表（逗号分隔）")
    p.add_argument("--event-id", dest="event_id", default="", help="关联的任务事件 ID")
    p.add_argument("--title", dest="title", default="", help="自定义事件任务标题")
    p.add_argument("--resume", action="store_true", help="是否为断点续跑模式")
    p.add_argument("--skip-upload", action="store_true", help="完成补扫后跳过自动上传 VPS")
    return p.parse_args()


def main():
    args = parse_args()
    py_exe = sys.executable

    try:
        target_dates = []
        if getattr(args, "resume", False) and args.event_id.strip():
            try:
                from scripts import task_event_db
                target_dates = task_event_db.get_unfinished_dates(args.event_id.strip())
                target_dates = list(reversed(target_dates))
            except Exception as e_ev:
                print(f"[警告] 从事件 ID 加载未完成日期失败: {e_ev}")
        elif args.dates.strip():
            raw_dates = [d.strip() for d in args.dates.split(",") if d.strip()]
            for rd in raw_dates:
                try:
                    datetime.strptime(rd, "%Y-%m-%d")
                    target_dates.append(rd)
                except Exception:
                    print(f"[警告] 忽略无效日期参数: {rd}")
            target_dates = sorted(list(set(target_dates)), reverse=True)
        elif args.start_date.strip() and args.end_date.strip():
            d_start = datetime.strptime(args.start_date.strip(), "%Y-%m-%d").date()
            d_end = datetime.strptime(args.end_date.strip(), "%Y-%m-%d").date()
            if d_start > d_end:
                d_start, d_end = d_end, d_start
            curr = d_start
            while curr <= d_end:
                target_dates.append(curr.isoformat())
                curr += timedelta(days=1)
            target_dates.reverse()
        elif args.event_id.strip():
            try:
                from scripts import task_event_db
                target_dates = task_event_db.get_unfinished_dates(args.event_id.strip())
                target_dates = list(reversed(target_dates))
            except Exception as e_ev:
                print(f"[警告] 从事件 ID 加载未完成日期失败: {e_ev}")
        else:
            print("[错误] 必须提供 --start-date 与 --end-date，或者通过 --dates 指定日期列表")
            sys.exit(1)
    except Exception as e_date:
        print(f"[错误] 解析扫描日期失败: {e_date}")
        sys.exit(1)

    if not target_dates:
        print("[错误] 未指定任何有效的扫描日期")
        sys.exit(1)

    # 引入任务事件台账组件
    event_id = args.event_id.strip()
    try:
        from scripts import task_event_db
        task_event_db.init_db()
        if not event_id:
            event_id = task_event_db.create_event(
                task_type="batch_scan",
                target_dates=target_dates,
                title=args.title.strip() or None,
                params={"start_date": args.start_date, "end_date": args.end_date, "skip_upload": args.skip_upload}
            )
    except Exception as e_evt:
        print(f"[提示] 事件任务台账初始化提示: {e_evt}")
        task_event_db = None

    print("=" * 68)
    mode_tag = "【断点续跑】" if args.resume else "【批量扫描】"
    print(f"[*] 启动{mode_tag}事件任务：共 {len(target_dates)} 天 ({target_dates[0]} 至 {target_dates[-1]})", flush=True)
    if event_id:
        print(f"[*] 关联事件任务 ID: {event_id}", flush=True)
        os.environ["GXZB_EVENT_ID"] = event_id
    os.environ["GXZB_TASK_TYPE"] = "event"
    print("=" * 68)

    success_days = []
    failed_days = []

    for idx, day_str in enumerate(target_dates, 1):
        t_start = time.time()
        pct = int(idx / len(target_dates) * 100)
        print(f"\n" + "-" * 50, flush=True)
        print(f">>> [{idx}/{len(target_dates)} - 进度 {pct}%] 正在补扫执行日期: {day_str} ...", flush=True)
        if task_event_db and event_id:
            try:
                task_event_db.update_day_progress(event_id, day_str, status="running")
            except Exception:
                pass
        try:
            sub_env = os.environ.copy()
            sub_env["COLLECT_NO_RECORD_RUN"] = "1"
            sub_env["GXZB_TASK_TYPE"] = "event"
            if event_id:
                sub_env["GXZB_EVENT_ID"] = event_id

            # 1. 执行单日采集 (含公共资源交易中心与崇左阳光采购)
            print(f"    [步骤 1/3] 抓取当日公告数据 (公共资源 + 崇左阳光采购)...", flush=True)
            cmd_collect = [py_exe, "-u", str(ROOT_DIR / "scripts" / "collect.py"), "--date", day_str, "--no-record-run"]
            try:
                res1 = subprocess.run(cmd_collect, env=sub_env, timeout=180)
                if res1.returncode != 0:
                    print(f"    [警告] {day_str} 采集脚本退出码异常: {res1.returncode}", flush=True)
            except subprocess.TimeoutExpired:
                print(f"    [超时] {day_str} 公共资源采集超时 (180s)，强制中断进入下一步", flush=True)
                res1 = None

            cmd_cz = [py_exe, "-u", str(ROOT_DIR / "scripts" / "collect_cz_ygcg.py"), "--date", day_str]
            try:
                subprocess.run(cmd_cz, env=sub_env, timeout=90)
            except subprocess.TimeoutExpired:
                print(f"    [超时] {day_str} 崇左阳光采购采集超时 (90s)，跳过", flush=True)

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
            try:
                res2 = subprocess.run(cmd_build, env=sub_env, timeout=60)
            except subprocess.TimeoutExpired:
                print(f"    [超时] {day_str} 静态页生成超时 (60s)，跳过", flush=True)
                res2 = None
            elapsed = time.time() - t_start

            # 查询当日抓取公告总数与重点公告数
            notice_cnt = 0
            focus_cnt = 0
            try:
                import sqlite3
                with sqlite3.connect(str(config.DB_PATH)) as conn:
                    row_all = conn.execute("SELECT count(*) FROM notices WHERE pub_time LIKE ?", (f"{day_str}%",)).fetchone()
                    notice_cnt = row_all[0] if row_all else 0
                    row_foc = conn.execute("SELECT count(*) FROM notices WHERE pub_time LIKE ? AND (industry LIKE '%重点%' OR title LIKE '%重点%' OR title LIKE '%重大%')", (f"{day_str}%",)).fetchone()
                    focus_cnt = row_foc[0] if row_foc else 0
            except Exception:
                pass

            if res2.returncode == 0:
                success_days.append(day_str)
                print(f"[OK] 日期 {day_str} 采集并构建完成！(耗时: {elapsed:.1f}s)", flush=True)
                try:
                    refresh_archive(day_str)
                except Exception:
                    pass
                if task_event_db and event_id:
                    try:
                        task_event_db.update_day_progress(
                            event_id, day_str, status="success", count=notice_cnt, focus=focus_cnt, duration=elapsed
                        )
                    except Exception:
                        pass
            else:
                failed_days.append(day_str)
                print(f"[FAIL] 日期 {day_str} 构建失败，返回码: {res2.returncode} (耗时: {elapsed:.1f}s)", flush=True)
                if task_event_db and event_id:
                    try:
                        task_event_db.update_day_progress(
                            event_id, day_str, status="failed", duration=elapsed, error=f"build_daily_page exit code {res2.returncode}"
                        )
                    except Exception:
                        pass
        except Exception as e_day:
            failed_days.append(day_str)
            print(f"[FAIL] 日期 {day_str} 调度过程发生未捕获异常: {e_day}，继续处理下一日...", flush=True)
            if task_event_db and event_id:
                try:
                    task_event_db.update_day_progress(
                        event_id, day_str, status="failed", duration=time.time() - t_start, error=str(e_day)
                    )
                except Exception:
                    pass

    # 4. 全部补扫完成后，全量重构归档总表（index.html）
    print("\n" + "=" * 68, flush=True)
    print("[*] 所有指定日期已完成扫描，正在刷新更新历史归档总索引 (index.html) ...", flush=True)
    subprocess.run([py_exe, "-u", str(ROOT_DIR / "scripts" / "build_archive_page.py")])

    # 5. 若配置了自动上传，统一同步云端
    if getattr(config, "IS_ON_VPS", False):
        print("\n[*] 当前直接运行于生产 VPS，全量补扫数据与静态网页已就地由 Nginx 实时响应生效！", flush=True)
    elif not args.skip_upload and getattr(config, "AUTO_UPLOAD_VPS", True):
        print("\n[*] 正在将全量补扫数据与网页同步发布到云端服务器 ...", flush=True)
        upload_script = ROOT_DIR / "scripts" / "upload_vps.py"
        if upload_script.exists():
            subprocess.run([py_exe, "-u", str(upload_script)])

    print("\n" + "=" * 68)
    print(f"[*] 批量补扫总结: 成功 {len(success_days)} 天，失败 {len(failed_days)} 天", flush=True)
    if failed_days:
        print(f"失败日期列表: {failed_days}", flush=True)
    print("=" * 68)

    # 完成事件任务归档
    if task_event_db and event_id:
        try:
            task_event_db.finish_event(event_id)
        except Exception as e_fin:
            print(f"[提示] 事件任务归档提示: {e_fin}")


if __name__ == "__main__":
    main()

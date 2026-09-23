# -*- coding: utf-8 -*-
"""每日任务编排：一条命令跑完「采集 -> 入库 -> 生成页面 -> 刷新归档 -> 核验 -> 可选推送」。

用法:
    python run_daily.py                       # 跑今天（按 .env 时区）
    python run_daily.py --date 2026-09-10
    python run_daily.py --skip-collect        # 只重生成页面（数据已就绪时）
    python run_daily.py --no-push             # 跳过外部推送命令
    python run_daily.py --strict              # 存在缺失条目时退出码 2（便于监控告警）

设计目标（脱离 AI 自动运行）:
    - 只依赖 Python 标准库 + 本仓库脚本，任何一步失败都返回非 0 退出码；
    - 每一步的产物路径固定、可追溯，日志与台账均以官方 infoid 为键；
    - 可直接挂 cron / launchd / 计划任务，无需人工干预、无需 AI 参与。

crontab 示例（每天 08:30 采集、17:30 再跑一遍）:
    30 8,17 * * * cd /opt/gxzb-daily && /usr/bin/python3 run_daily.py >> logs/cron.log 2>&1
"""
from datetime import datetime, time, timedelta

def is_within_schedule(start_str: str, end_str: str) -> bool:
    """判断当前时刻是否处于设定的监控时间段内（支持跨午夜）"""
    if not start_str or not end_str:
        return True
    try:
        now_t = datetime.now().time()
        s_h, s_m = map(int, start_str.split(":"))
        e_h, e_m = map(int, end_str.split(":"))
        s_t = time(s_h, s_m)
        e_t = time(e_h, e_m)
        if s_t <= e_t:
            return s_t <= now_t <= e_t
        else:
            return now_t >= s_t or now_t <= e_t
    except Exception:
        return True

def is_time_in_range(now_str: str, start_str: str, end_str: str) -> bool:
    """判断指定时间字符串 HH:MM 是否处于 start_str ~ end_str 区间（支持跨午夜）"""
    try:
        n_h, n_m = map(int, now_str.split(":"))
        s_h, s_m = map(int, start_str.split(":"))
        e_h, e_m = map(int, end_str.split(":"))
        now_t = time(n_h, n_m)
        s_t = time(s_h, s_m)
        e_t = time(e_h, e_m)
        if s_t <= e_t:
            return s_t <= now_t <= e_t
        else:
            return now_t >= s_t or now_t <= e_t
    except Exception:
        return False

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
import config      # noqa: E402
import logstore    # noqa: E402
import diff_missing  # noqa: E402

PY = sys.executable
SCRIPT = REPO / "scripts"


def banner(step, text):
    print("\n[%s] %s %s" % (config.now_stamp(), step, text), flush=True)


def run(args, allow_codes=(0,)):
    """执行子脚本并回显输出；返回 (退出码, 是否可接受)。兼容 PyInstaller 打包环境。"""
    print("$ " + " ".join(str(a) for a in args), flush=True)
    
    # 在 PyInstaller 单文件/打包环境中，sys.executable 是 exe 本身，无法直接解释其他 py 文件
    # 优先检测是否打包环境或目标脚本可以直接在当前进程使用 runpy 运行
    if getattr(sys, 'frozen', False) and len(args) >= 2 and str(args[0]) == sys.executable:
        script_path = Path(args[1])
        if script_path.suffix == ".py" and script_path.exists():
            import runpy
            old_argv = sys.argv[:]
            sys.argv = [str(script_path)] + [str(a) for a in args[2:]]
            ret_code = 0
            try:
                runpy.run_path(str(script_path), run_name="__main__")
            except SystemExit as se:
                ret_code = se.code if isinstance(se.code, int) else (1 if se.code else 0)
            except Exception as e:
                print(f"[Error in runpy {script_path.name}]: {e}", file=sys.stderr)
                ret_code = 1
            finally:
                sys.argv = old_argv
            return ret_code, ret_code in allow_codes

    proc = subprocess.run([str(a) for a in args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.stdout:
        print(proc.stdout.rstrip(), flush=True)
    if proc.stderr:
        print(proc.stderr.rstrip(), file=sys.stderr, flush=True)
    return proc.returncode, proc.returncode in allow_codes


# ------------------------------------------------------------------ 入库合并
def reconcile(day, mode):
    """把当日全量采集中「页面还没有」的条目并入当日入库文件。

    以官方 infoid 判重，只增不改，绝不删除已有条目；mode=report 时只统计不动数据。
    """
    collect_file = config.COLLECT_DIR / ("%s.json" % day)
    daily_file = config.DAILY_DIR / ("%s.json" % day)
    if not collect_file.exists():
        print("   无采集文件，跳过合并：", collect_file)
        return 0
    collected = json.loads(collect_file.read_text(encoding="utf-8"))
    # 兜底核验：如果当前采集结果尚未包含崇左阳光采购数据，自动补充采集
    has_cz = any(r.get("source") == "崇左阳光采购" or "cz.gxygcg.com" in str(r.get("link", "")) for r in collected)
    if not has_cz:
        try:
            collect_cz_func = None
            try:
                from scripts.collect_cz_ygcg import collect_cz_ygcg as collect_cz_func
            except (ImportError, ModuleNotFoundError):
                try:
                    from collect_cz_ygcg import collect_cz_ygcg as collect_cz_func
                except (ImportError, ModuleNotFoundError):
                    import importlib.util
                    cz_path = SCRIPT / "collect_cz_ygcg.py"
                    if cz_path.exists():
                        spec = importlib.util.spec_from_file_location("collect_cz_ygcg", cz_path)
                        mod = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(mod)
                        collect_cz_func = getattr(mod, "collect_cz_ygcg", None)
            if collect_cz_func:
                cz_rows, _ = collect_cz_func(day)
                if cz_rows:
                    c_ids = {str(r.get("infoid")) for r in collected if r.get("infoid")}
                    for cr in cz_rows:
                        if str(cr.get("infoid")) not in c_ids:
                            collected.append(cr)
                            c_ids.add(str(cr.get("infoid")))
                    collect_file.write_text(json.dumps(collected, ensure_ascii=False, indent=1), encoding="utf-8")
                    print("   [补充] 崇左阳光采购平台自动补充入库 %d 条" % len(cz_rows))
        except Exception as exc_cz_rec:
            print("   [提示] 崇左阳光采购对账补充抓取异常：", exc_cz_rec)

    daily = json.loads(daily_file.read_text(encoding="utf-8")) if daily_file.exists() else []
    known = {str(r.get("infoid") or r.get("id")) for r in daily}
    added = [r for r in collected if str(r.get("infoid")) not in known]
    # 对已有记录进行字段修补（例如此前存在 pub_time 为空或崇左阳光采购字段不完整的情况）
    coll_map = {str(r.get("infoid")): r for r in collected if r.get("infoid")}
    repaired = 0
    updated_daily = []
    for r in daily:
        iid = str(r.get("infoid") or r.get("id"))
        if iid in coll_map:
            cr = coll_map[iid]
            if cr.get("source") == "崇左阳光采购" or not r.get("pub_time") or "gxygcg.com" in str(cr.get("link", "")):
                r = {**r, **cr}
                repaired += 1
        updated_daily.append(r)
    daily = updated_daily

    print("   采集 %d 条 | 已入库 %d 条 | 待补 %d 条" % (len(collected), len(daily), len(added)))
    if mode != "merge" or (not added and repaired == 0):
        return len(added)
    merged = daily + added
    merged.sort(key=lambda r: (str(r.get("pub_time") or ""), str(r.get("infoid"))))
    daily_file.parent.mkdir(parents=True, exist_ok=True)
    daily_file.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
    print("   已合并入库，现共 %d 条 -> %s" % (len(merged), daily_file))
    return len(added)


# ------------------------------------------------------------------ 归档数据
def refresh_archive(day):
    """用当日入库数据刷新 <DATA_DIR>/archive.json（归档首页的数据源）。"""
    daily_file = config.DAILY_DIR / ("%s.json" % day)
    if not daily_file.exists():
        return
    rows = json.loads(daily_file.read_text(encoding="utf-8"))
    archive_file = config.DATA_DIR / "archive.json"
    archive = {"site": "广西全区招投标公告日报", "subtitle": "广西公共资源交易 · 崇左阳光采购公告每日归档",
               "generated": config.now_stamp(), "day_count": 0, "total_all": 0, "days": []}
    if archive_file.exists():
        try:
            archive.update(json.loads(archive_file.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            pass
    groups = {}
    for r in rows:
        groups[r.get("industry", "")] = groups.get(r.get("industry", ""), 0) + 1
    valid_pubs = [r.get("pub_time") for r in rows if r.get("pub_time")]
    latest_pub = max(valid_pubs) if valid_pubs else ""
    final_file = config.STATE_DIR / ("final-%s.json" % day)
    is_final_entry = final_file.exists()
    finalized_at = ""
    if is_final_entry:
        try:
            fin_d = json.loads(final_file.read_text(encoding="utf-8"))
            finalized_at = fin_d.get("finalized_at", "")
        except Exception:
            pass
    entry = {"date": day, "file": "%s.html" % day, "total": len(rows),
             "cities": len({r.get("areaname", "") for r in rows}),
             "cat_count": len({r.get("industry", "") for r in rows}),
             "groups": groups, "latest_pub": latest_pub, "updated": config.now_stamp(),
             "is_final": is_final_entry, "finalized_at": finalized_at}
    days = [d for d in archive.get("days", []) if d.get("date") != day] + [entry]
    days.sort(key=lambda d: d.get("date", ""), reverse=True)
    archive["days"] = days
    archive["day_count"] = len(days)
    archive["total_all"] = sum(int(d.get("total") or 0) for d in days)
    archive["generated"] = config.now_stamp()
    archive_file.write_text(json.dumps(archive, ensure_ascii=False, indent=1), encoding="utf-8")
    print("   归档数据：%d 天 / 共 %d 条 -> %s" % (archive["day_count"], archive["total_all"], archive_file))


# ------------------------------------------------------------------ 主流程
def main(argv=None):
    ap = argparse.ArgumentParser(description="每日任务编排（采集 → 生成 → 核验）")
    ap.add_argument("--date", default=None, help="指定目标日期 YYYY-MM-DD，支持 'yesterday' 或留空默认今天")
    ap.add_argument("--yesterday", "--yesterday-final", dest="yesterday_final", action="store_true", help="指定目标为昨天，并执行最终扫描封存为最终版")
    ap.add_argument("--final", action="store_true", help="将当前目标日期标记并保存为全天最终版")
    ap.add_argument("--skip-collect", action="store_true")
    ap.add_argument("--skip-backscan", action="store_true", help="跳过历史回扫排查")
    ap.add_argument("--backscan-days", type=int, default=getattr(config, "BACKSCAN_DAYS", 30), help="历史回扫天数（默认 30）")
    ap.add_argument("--min-delay", type=int, default=getattr(config, "BACKSCAN_MIN_DELAY", 2), help="滞后判定阈值天数（默认 2）")
    ap.add_argument("--skip-archive", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--strict", action="store_true", help="存在缺失条目时退出码 2")
    ap.add_argument("--force", action="store_true", help="忽略监控时间窗口限制强制执行")
    ap.add_argument("--reconcile", choices=["report", "merge"], default=config.RECONCILE)
    args = ap.parse_args(argv)

    config.ensure_dirs()
    now = datetime.now()
    now_str = now.strftime("%H:%M")
    today_str = config.today()
    yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    final_marker = config.STATE_DIR / ("final-%s.json" % yesterday_str)

    is_final = False

    if args.yesterday_final or (args.date and args.date.lower() == "yesterday"):
        day = yesterday_str
        is_final = True
    elif args.date:
        day = args.date
        is_final = bool(args.final)
    else:
        # 未显式指定日期（由 Windows 计划任务等自动化调度触发）
        enable_yesterday_final = getattr(config, "ENABLE_YESTERDAY_FINAL", True)
        # 凌晨 00:10 最终扫描窗口：00:05 ~ 00:25
        in_midnight_window = is_time_in_range(now_str, "00:05", "00:25")

        if enable_yesterday_final and in_midnight_window and not final_marker.exists():
            print(f"[*] 检测到当前处于凌晨 00:10 最终扫描窗口 ({now_str})，自动启动昨日 ({yesterday_str}) 标讯最终版扫描与封存！")
            day = yesterday_str
            is_final = True
        else:
            day = today_str
            is_final = bool(args.final)

    print("=" * 68)
    print("广西招投标公告日报 · 任务 %s%s" % (day, " (全天最终版)" if is_final else ""))
    print("站点目录 :", config.SITE_DIR)
    print("数据目录 :", config.DATA_DIR)
    print("入库口径 :", args.reconcile)
    print("=" * 68)

    failed_steps = []

    # 0) 监控时间窗口检查（支持定时任务自动跳过非工作时段）
    monitor_start = getattr(config, "MONITOR_START_TIME", "08:00")
    monitor_end = getattr(config, "MONITOR_END_TIME", "20:00")
    # 终版封存任务（如 00:10 自动触发）不受日间监控时间窗限制
    if not is_final and not args.force and not args.skip_collect and not is_within_schedule(monitor_start, monitor_end):
        now_str = datetime.now().strftime("%H:%M")
        print(f"[*] 当前时间 {now_str} 不在监控时间段 [{monitor_start} - {monitor_end}] 内，跳过本次采集与同步。")
        print("    （如需手动强制执行，可加 --force 参数）")
        return 0

    # 日间正常监控时段（如早晨 08:00 开机）：若昨日终版尚未封存（夜间电脑关机），自动先补跑昨日终版封存
    if not args.date and not is_final and getattr(config, "ENABLE_YESTERDAY_FINAL", True) and not final_marker.exists():
        print(f"[*] 检测到昨日 ({yesterday_str}) 终版尚未封存（夜间可能休眠/关机），先补跑昨日最终版归档...")
        try:
            rc_y, ok_y = run([PY, str(SCRIPT / "run_yesterday_final.py"), "--force"], allow_codes=(0, 1))
            if ok_y:
                print(f"[+] 昨日 ({yesterday_str}) 终版补跑封存完成！")
            else:
                print(f"[!] 昨日终版补跑退出码为 {rc_y}")
        except Exception as e_catchup:
            print(f"[!] 昨日终版补跑异常: {e_catchup}")

    # 1) 采集
    if args.skip_collect:
        banner("1/8", "采集：已跳过（--skip-collect）")
    else:
        banner("1/8", "采集当日全量公告")
        rc, ok = run([PY, SCRIPT / "collect.py", "--date", day], allow_codes=(0, 3))
        # 并发/顺次采集【崇左阳光采购平台】工程类公告
        try:
            rc_cz, ok_cz = run([PY, SCRIPT / "collect_cz_ygcg.py", "--date", day], allow_codes=(0, 3))
        except Exception as exc_cz:
            print("崇左阳光采购平台采集异常：", exc_cz)

        # 1.2) 定时回扫过去 30 天公告（排查滞后补录/隐匿现身项目）
        if getattr(config, "BACKSCAN_ENABLED", True) and not args.skip_backscan:
            banner("1.2/8", f"定时回扫历史公告（排查过去 {args.backscan_days} 天滞后补录/隐匿现身项目）")
            try:
                rc_bs, ok_bs = run([
                    PY, SCRIPT / "backscan_delayed.py",
                    "--date", day,
                    "--days", str(args.backscan_days),
                    "--min-delay", str(args.min_delay),
                ], allow_codes=(0, 2))
                if not ok_bs:
                    failed_steps.append("backscan_delayed")
            except Exception as exc_bs:
                print("历史回扫排查步骤异常：", exc_bs)

        if not ok:
            failed_steps.append("collect")
            print("采集全部失败，终止后续步骤（保留旧日志与旧页面）")
            if getattr(config, "WECHAT_APPID", "") and getattr(config, "WECHAT_TOUSER", ""):
                try:
                    from scripts.notify_wechat import send_alert
                    send_alert(day, "采集步骤全部失败，已中断后续生成", "collect")
                except Exception as exc:
                    print("告警推送异常：", exc)
            return finish(day, failed_steps, args)

    # 2) 入库对账 / 合并
    banner("2/8", "入库对账（缺失 = 全量 - 已入库）")
    try:
        reconcile(day, args.reconcile)
    except Exception as exc:                                   # noqa: BLE001
        failed_steps.append("reconcile")
        print("入库对账失败：", exc)

    # 3) 生成每日页
    banner("3/8", "生成每日明细页")
    build_cmd = [PY, SCRIPT / "build_daily_page.py", "--date", day]
    if is_final:
        build_cmd.append("--final")
    rc, ok = run(build_cmd)
    if not ok:
        failed_steps.append("build_daily_page")
        if getattr(config, "WECHAT_APPID", "") and getattr(config, "WECHAT_TOUSER", ""):
            try:
                from scripts.notify_wechat import send_alert
                send_alert(day, "明细页生成失败，已中断后续流程", "build_daily_page")
            except Exception as exc:
                print("告警推送异常：", exc)
        return finish(day, failed_steps, args)

    # 4) 归档首页
    if args.skip_archive:
        banner("4/8", "归档首页：已跳过（--skip-archive）")
    else:
        banner("4/8", "刷新归档首页")
        try:
            refresh_archive(day)
        except Exception as exc:                               # noqa: BLE001
            failed_steps.append("archive_data")
            print("归档数据刷新失败：", exc)
        if config.TEMPLATE_ARCHIVE_SAMPLE.exists():
            rc, ok = run([PY, SCRIPT / "build_archive_page.py"], allow_codes=(0,))
            if not ok:
                failed_steps.append("build_archive_page")
        else:
            print("   未找到归档样板，跳过：", config.TEMPLATE_ARCHIVE_SAMPLE)

    # 5) 核验
    banner("5/8", "核验页面缺失条目")
    res = {}
    try:
        res = diff_missing.diff(day)
        print("   全量 %d 条 | 页面 %d 条 | 缺失 %d 条（早前漏采 %d / 快照后新增 %d）| 页面独有 %d 条"
              % (res["collect_unique"], res["page_total"], res["missing_count"],
                 res.get("stale_missed_count", 0), res.get("pending_count", 0),
                 res["extra_count"]))
        print("   报告：", res.get("report_md"))
    except Exception as exc:                                   # noqa: BLE001
        failed_steps.append("diff_missing")
        print("核验失败：", exc)

    # 6) 外部推送（可选）
    if config.PUSH_CMD and not args.no_push:
        banner("6/8", "执行外部推送命令")
        rc, ok = run(config.PUSH_CMD.split(), allow_codes=(0,))
        if not ok:
            failed_steps.append("push")
    else:
        banner("6/8", "外部推送：未配置或已跳过")


    # 7) 自动同步 VPS（可选）
    if getattr(config, "AUTO_UPLOAD_VPS", True):
        banner("7/8", "同步上传静态站点至 VPS")
        rc, ok = run([PY, SCRIPT / "upload_vps.py"], allow_codes=(0,))
        if not ok:
            failed_steps.append("upload_vps")

    # 8) 微信服务号模版推送（可选）
    if getattr(config, "WECHAT_APPID", "") and getattr(config, "WECHAT_TOUSER", ""):
        banner("8/8", "微信服务号模版消息推送")
        try:
            total_cnt = int(res.get("page_total") or res.get("collect_unique") or 0)
            # 统计重点条目
            focus_num = 0
            page_path = config.SITE_DIR / f"{day}.html"
            max_amount = 0.0
            daily_json = config.DAILY_DIR / f"{day}.json"
            if daily_json.exists():
                try:
                    with open(daily_json, "r", encoding="utf-8") as f:
                        _items = json.load(f)
                        if isinstance(_items, list):
                            for _it in _items:
                                _amt = config.extract_amount_from_title(_it.get("title", ""))
                                if _amt and _amt > max_amount:
                                    max_amount = _amt
                except Exception:
                    pass
            if page_path.exists():
                try:
                    txt = page_path.read_text(encoding="utf-8")
                    import re
                    m = re.search(r"重点关注\s*\((\d+)\)", txt)
                    if m:
                        focus_num = int(m.group(1))
                except Exception:
                    pass
            from scripts.notify_wechat import send_daily_summary, send_alert, check_push_condition
            should_push, reason = check_push_condition(force=is_final or args.force, total_count=total_cnt, focus_count=focus_num, max_amount=max_amount)
            if not should_push:
                print(f"[微信推送跳过] {reason}")
            else:
                print(f"[微信推送执行] {reason}")
                send_daily_summary(day, total_count=total_cnt, focus_count=focus_num, failed_steps=failed_steps, is_final=is_final)
        except Exception as exc:
            print("微信推送异常：", exc)

    return finish(day, failed_steps, args, res, is_final=is_final)


def finish(day, failed_steps, args, res=None, is_final=False):
    res = res or {}
    missing = int(res.get("missing_count") or 0)
    now_stamp = config.now_stamp()
    summary = {"date": day, "finished_at": now_stamp,
               "failed_steps": failed_steps, "missing_count": missing,
               "stale_missed_count": int(res.get("stale_missed_count") or 0),
               "pending_count": int(res.get("pending_count") or 0),
               "collect_total": res.get("collect_unique"), "page_total": res.get("page_total"),
               "report_md": res.get("report_md"),
               "is_final": is_final}
    if is_final:
        summary["finalized_at"] = now_stamp
    try:
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)
        (config.STATE_DIR / ("run-%s.json" % day)).write_text(
            json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        if is_final and not failed_steps:
            final_summary = {
                "date": day,
                "finalized_at": now_stamp,
                "is_final": True,
                "collect_total": summary["collect_total"] or summary["page_total"] or 0,
                "page_total": summary["page_total"] or summary["collect_total"] or 0,
                "missing_count": summary["missing_count"],
                "status": "sealed"
            }
            (config.STATE_DIR / ("final-%s.json" % day)).write_text(
                json.dumps(final_summary, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass

    try:
        from store import get_conn, record_run
        with get_conn() as conn:
            record_run(
                conn,
                run_id=None,
                day=day,
                kind="yesterday_final" if is_final else "daily",
                stats={
                    "total": summary["collect_total"] or summary["page_total"] or 0,
                    "finished_at": summary["finished_at"],
                    "failed": len(failed_steps),
                    "missing": summary["missing_count"]
                },
                ok=1 if not failed_steps else 0
            )
    except Exception:
        pass

    print("\n" + "=" * 68)
    print("任务结束 | 失败步骤：%s" % ("、".join(failed_steps) if failed_steps else "无"))
    if failed_steps:
        print("退出码 1（需人工排查）")
        return 1
    if args.strict and missing:
        print("退出码 2（缺失 %d 条，--strict 模式）" % missing)
        return 2
    print("退出码 0（正常）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

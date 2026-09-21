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
from datetime import datetime, time

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

    proc = subprocess.run([str(a) for a in args], capture_output=True, text=True)
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
    daily = json.loads(daily_file.read_text(encoding="utf-8")) if daily_file.exists() else []
    known = {str(r.get("infoid") or r.get("id")) for r in daily}
    added = [r for r in collected if str(r.get("infoid")) not in known]
    print("   采集 %d 条 | 已入库 %d 条 | 待补 %d 条" % (len(collected), len(daily), len(added)))
    if mode != "merge" or not added:
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
    archive = {"site": "广西招投标公告日报", "subtitle": "广西公共资源交易 · 工程建设类公告每日归档",
               "generated": config.now_stamp(), "day_count": 0, "total_all": 0, "days": []}
    if archive_file.exists():
        try:
            archive.update(json.loads(archive_file.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            pass
    groups = {}
    for r in rows:
        groups[r.get("industry", "")] = groups.get(r.get("industry", ""), 0) + 1
    entry = {"date": day, "file": "%s.html" % day, "total": len(rows),
             "cities": len({r.get("areaname", "") for r in rows}),
             "cat_count": len({r.get("industry", "") for r in rows}),
             "groups": groups, "updated": config.now_stamp()}
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
    ap.add_argument("--date", default=None)
    ap.add_argument("--skip-collect", action="store_true")
    ap.add_argument("--skip-archive", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--strict", action="store_true", help="存在缺失条目时退出码 2")
    ap.add_argument("--force", action="store_true", help="忽略监控时间窗口限制强制执行")
    ap.add_argument("--reconcile", choices=["report", "merge"], default=config.RECONCILE)
    args = ap.parse_args(argv)

    config.ensure_dirs()
    day = args.date or config.today()
    print("=" * 68)
    print("广西招投标公告日报 · 每日任务 %s" % day)
    print("站点目录 :", config.SITE_DIR)
    print("数据目录 :", config.DATA_DIR)
    print("入库口径 :", args.reconcile)
    print("=" * 68)

    failed_steps = []

    # 0) 监控时间窗口检查（支持定时任务自动跳过非工作时段）
    monitor_start = getattr(config, "MONITOR_START_TIME", "08:00")
    monitor_end = getattr(config, "MONITOR_END_TIME", "20:00")
    if not args.force and not args.skip_collect and not is_within_schedule(monitor_start, monitor_end):
        now_str = datetime.now().strftime("%H:%M")
        print(f"[*] 当前时间 {now_str} 不在监控时间段 [{monitor_start} - {monitor_end}] 内，跳过本次采集与同步。")
        print("    （如需手动强制执行，可加 --force 参数）")
        return 0

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
    rc, ok = run([PY, SCRIPT / "build_daily_page.py", "--date", day])
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
    if getattr(config, "AUTO_UPLOAD_VPS", False):
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
            should_push, reason = check_push_condition(force=False, total_count=total_cnt, focus_count=focus_num)
            if not should_push:
                print(f"[微信推送跳过] {reason}")
            else:
                print(f"[微信推送执行] {reason}")
                send_daily_summary(day, total_count=total_cnt, focus_count=focus_num, failed_steps=failed_steps)
        except Exception as exc:
            print("微信推送异常：", exc)

    return finish(day, failed_steps, args, res)


def finish(day, failed_steps, args, res=None):
    res = res or {}
    missing = int(res.get("missing_count") or 0)
    summary = {"date": day, "finished_at": config.now_stamp(),
               "failed_steps": failed_steps, "missing_count": missing,
               "stale_missed_count": int(res.get("stale_missed_count") or 0),
               "pending_count": int(res.get("pending_count") or 0),
               "collect_total": res.get("collect_unique"), "page_total": res.get("page_total"),
               "report_md": res.get("report_md")}
    try:
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)
        (config.STATE_DIR / ("run-%s.json" % day)).write_text(
            json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
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

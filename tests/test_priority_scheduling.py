# -*- coding: utf-8 -*-
"""优先级避让与双向协作调度测试套件

测试场景：
1. 无自动任务采集时，手动任务一路畅通直接启动执行；
2. 手动任务执行中，自动任务进入采集阶段，手动任务主动暂停挂起；自动任务完成采集后，手动任务毫秒级恢复；
3. 自动任务正在采集中时，启动手动任务，手动任务成功创建并挂起排队，采集完成后自动开始；
4. 僵尸死锁与超时（TTL）自愈机制测试。
"""
import sys
import os
import time
import json
import threading
from pathlib import Path

# 确保项目根目录在 sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config
from scripts.priority_lock import (
    PriorityLock,
    wait_for_priority_task,
    is_priority_collecting,
    get_running_lock_info,
    LOCK_FILE,
    DEFAULT_LOCK_TTL_SECONDS,
)


def cleanup_lock():
    try:
        if LOCK_FILE.exists():
            LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def test_scenario_idle():
    """场景1：无自动任务采集时，直接放行"""
    cleanup_lock()
    assert not is_priority_collecting(), "空闲状态下 is_priority_collecting 应为 False"
    waited = wait_for_priority_task(poll_interval=0.1)
    assert not waited, "空闲状态下 wait_for_priority_task 不应阻塞等待"
    print("✅ 场景1 通过：空闲状态下手动任务无阻塞直接放行")


def test_scenario_manual_pauses_for_auto():
    """场景2：手动任务运行期间，自动任务开始采集，手动任务主动挂起；采集完成后自动恢复"""
    cleanup_lock()
    lock = PriorityLock(task_name="自动日更测试", ttl_seconds=10)
    lock.acquire(phase="collecting")

    # 验证采集状态已被激活
    assert is_priority_collecting(), "持锁后 is_priority_collecting 应为 True"

    resume_event = threading.Event()
    wait_finished = threading.Event()

    def mock_manual_task():
        # 手动任务模拟在迭代中检查锁
        # 注意：模拟另一个进程执行，临时清除 _CURRENT_PROCESS_HOLDS_LOCK 模拟外部进程调用
        import scripts.priority_lock as pl
        saved = pl._CURRENT_PROCESS_HOLDS_LOCK
        pl._CURRENT_PROCESS_HOLDS_LOCK = False
        try:
            waited = wait_for_priority_task(poll_interval=0.1, context="批量扫描模拟")
            if waited:
                resume_event.set()
        finally:
            pl._CURRENT_PROCESS_HOLDS_LOCK = saved
            wait_finished.set()

    th = threading.Thread(target=mock_manual_task)
    th.start()

    # 模拟手动任务在等待中
    time.sleep(0.3)
    assert not resume_event.is_set(), "自动任务采集中，手动任务必须处于挂起暂停状态"

    # 模拟自动任务完成采集阶段，切换为 finishing 阶段
    lock.set_phase("finishing")
    th.join(timeout=2.0)

    assert resume_event.is_set(), "自动任务采集阶段完成后，手动任务应自动恢复执行"
    assert not is_priority_collecting(), "finishing 阶段不再阻塞采集"
    lock.release()
    cleanup_lock()
    print("✅ 场景2 通过：自动任务到期采集时手动任务主动暂停，采完后毫秒级恢复")


def test_scenario_queued_creation():
    """场景3：自动任务采集中启动手动任务，允许创建并挂起排队，采完后自动运行"""
    cleanup_lock()
    lock = PriorityLock(task_name="自动日更测试", ttl_seconds=10)
    lock.acquire(phase="collecting")

    from manager.server import ProcessManager
    mgr = ProcessManager()

    # 模拟另一个进程环境调用
    import scripts.priority_lock as pl
    saved = pl._CURRENT_PROCESS_HOLDS_LOCK
    pl._CURRENT_PROCESS_HOLDS_LOCK = False

    try:
        cmd = [sys.executable, "-c", "import sys; print('manual done'); sys.exit(0)"]
        ok, msg = mgr.start_task("测试排队手动任务", cmd)
        assert ok, "自动任务采集中，手动任务应允许成功创建"
        assert mgr.status == "waiting_priority", "任务初始状态应为 waiting_priority 排队挂起中"

        state = mgr.get_state()
        assert state["running"] is True, "waiting_priority 状态下前端应能正常识别为活跃监控中"
        assert "优先级避让挂起中" in state["log"], "日志中应明确包含避让排队提示"

        # 模拟自动任务完成本阶段采集
        lock.set_phase("finishing")
        time.sleep(0.5)

        # 此时任务应该已经恢复运行并执行完毕
        max_wait = 80
        while max_wait > 0 and mgr.status in ("waiting_priority", "running"):
            time.sleep(0.1)
            max_wait -= 1

        assert mgr.status == "success", f"排队任务应在采集完成后自动恢复并成功结束，当前状态: {mgr.status}"
        assert "manual done" in mgr.get_state()["log"]
    finally:
        pl._CURRENT_PROCESS_HOLDS_LOCK = saved
        lock.release()
        cleanup_lock()

    print("✅ 场景3 通过：采集中创建手动任务可成功排队挂起，采完自动流转执行")


def test_scenario_deadlock_ttl_self_healing():
    """场景4：僵尸死锁与超时（TTL）自愈测试"""
    cleanup_lock()
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    # 模拟一个 20 分钟前创建的死锁
    stale_info = {
        "pid": os.getpid(),
        "task_name": "卡死进程模拟",
        "phase": "collecting",
        "started_at": now_str,
        "updated_at": now_str,
        "timestamp": time.time() - 1200,  # 20分钟前
        "ttl_seconds": 900,  # TTL 15分钟
        "pause_requested": True,
        "details": {},
    }
    LOCK_FILE.write_text(json.dumps(stale_info, ensure_ascii=False), encoding="utf-8")

    import scripts.priority_lock as pl
    saved = pl._CURRENT_PROCESS_HOLDS_LOCK
    pl._CURRENT_PROCESS_HOLDS_LOCK = False

    try:
        # 读取锁信息应自动自愈清理
        info = get_running_lock_info(check_collecting_only=True)
        assert info is None, "超时僵尸锁应被自动清理"
        assert not LOCK_FILE.exists(), "锁文件应已被自愈删除"
        assert not is_priority_collecting(), "自愈后 is_priority_collecting 应返回 False"
    finally:
        pl._CURRENT_PROCESS_HOLDS_LOCK = saved
        cleanup_lock()

    print("✅ 场景4 通过：超时僵尸锁自动清理自愈，防止任何死锁阻断手动任务")


if __name__ == "__main__":
    test_scenario_idle()
    test_scenario_manual_pauses_for_auto()
    test_scenario_queued_creation()
    test_scenario_deadlock_ttl_self_healing()
    print("\n🎉 所有优先级避让与双向协作测试用例全部通过！")

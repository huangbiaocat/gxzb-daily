# -*- coding: utf-8 -*-
"""高优先级任务锁与协作式让道/避让机制。

核心调度规则：
1. 优先级次序：自动定时任务（run_daily.py 采集阶段） > 手动任务（batch_scan / reapply_rules / 后台手动触发）。
2. 手动任务在跑时，若自动任务时间到了：
   自动任务发出采集互斥信号，手动任务在迭代间隙（中心/天/批次）主动暂停挂起；
   等自动任务完成本阶段采集后，手动任务自动唤醒并继续执行。
3. 自动任务处于挂起等待、或者未在进行核心采集时：
   允许手动任务正常启动并跑起来。
4. 启动手动任务时，若自动任务正在采集：
   系统允许先创建手动任务，但先挂起不采集，排队等待自动任务完成本阶段采集后再继续执行。
5. 超时自愈（Anti-Deadlock）：锁内置 15 分钟 TTL 与心跳机制，进程死亡或超时自动释放，绝不永久死锁。
"""
import os
import sys
import time
import json
import atexit
from pathlib import Path
from typing import Optional, Dict, Any

try:
    import config
    LOCK_FILE = config.DATA_DIR / ".priority_job.lock"
except Exception:
    LOCK_FILE = Path(__file__).resolve().parent.parent / "data" / ".priority_job.lock"

# 采集阶段最大允许无心跳超时时间（秒），默认 15 分钟
DEFAULT_LOCK_TTL_SECONDS = 900
# 标记当前进程是否为主持锁者（防止子任务误挂起自己）
_CURRENT_PROCESS_HOLDS_LOCK = False

def is_process_alive(pid: int) -> bool:
    """跨平台检查指定 PID 进程是否存活"""
    if not pid or pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            SYNCHRONIZE = 0x00100000
            h_proc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid)
            if not h_proc:
                return False
            exit_code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(h_proc, ctypes.byref(exit_code)):
                kernel32.CloseHandle(h_proc)
                STILL_ACTIVE = 259
                return exit_code.value == STILL_ACTIVE
            kernel32.CloseHandle(h_proc)
            return False
        except Exception:
            return True
    else:
        try:
            os.kill(pid, 0)
            return True
        except PermissionError:
            return True
        except (OSError, ProcessLookupError):
            return False


class PriorityLock:
    """高优先级自动任务运行期守护锁（支持 ContextManager、阶段流转与心跳）"""
    def __init__(self, task_name: str = "定时日更监控", details: Optional[Dict[str, Any]] = None, ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS):
        self.task_name = task_name
        self.details = details or {}
        self.ttl_seconds = ttl_seconds
        self.phase = "collecting"  # "collecting" | "finishing" | "idle"
        self.acquired = False

    def __enter__(self):
        self.acquire(phase=self.phase)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()

    def acquire(self, phase: str = "collecting"):
        global _CURRENT_PROCESS_HOLDS_LOCK
        try:
            LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
            self.phase = phase
            now_str = time.strftime("%Y-%m-%d %H:%M:%S")
            info = {
                "pid": os.getpid(),
                "task_name": self.task_name,
                "phase": self.phase,
                "started_at": now_str,
                "updated_at": now_str,
                "timestamp": time.time(),
                "ttl_seconds": self.ttl_seconds,
                "pause_requested": (self.phase == "collecting"),
                "details": self.details,
            }
            LOCK_FILE.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
            self.acquired = True
            _CURRENT_PROCESS_HOLDS_LOCK = True
            atexit.register(self.release)
        except Exception:
            pass

    def set_phase(self, phase: str):
        """切换任务执行阶段。若离开 collecting 阶段，则释放对手动任务的暂停阻塞。"""
        if not self.acquired:
            return
        self.phase = phase
        try:
            if LOCK_FILE.exists():
                info = get_running_lock_info(check_collecting_only=False)
                if info and info.get("pid") == os.getpid():
                    info["phase"] = phase
                    info["pause_requested"] = (phase == "collecting")
                    info["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    info["timestamp"] = time.time()
                    LOCK_FILE.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def heartbeat(self):
        """刷新心跳时间，防止超长采集被超时自愈机制误杀"""
        if not self.acquired:
            return
        try:
            if LOCK_FILE.exists():
                info = get_running_lock_info(check_collecting_only=False)
                if info and info.get("pid") == os.getpid():
                    info["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    info["timestamp"] = time.time()
                    LOCK_FILE.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def release(self):
        global _CURRENT_PROCESS_HOLDS_LOCK
        if self.acquired:
            try:
                if LOCK_FILE.exists():
                    info = get_running_lock_info(check_collecting_only=False)
                    if not info or info.get("pid") == os.getpid():
                        LOCK_FILE.unlink(missing_ok=True)
            except Exception:
                pass
            self.acquired = False
            _CURRENT_PROCESS_HOLDS_LOCK = False


def get_running_lock_info(check_collecting_only: bool = False) -> Optional[Dict[str, Any]]:
    """
    读取当前正在持有高优先级锁的信息。
    - 若对应进程已死亡，自动清理僵尸锁。
    - 若心跳时间已超过 TTL（默认15分钟），自动判定为卡死僵尸锁并自愈清理。
    - 若 check_collecting_only=True，仅当任务正处于核心采集阶段（phase="collecting"）时返回信息。
    """
    if not LOCK_FILE.exists():
        return None
    try:
        data = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
        pid = data.get("pid")
        if not pid or not is_process_alive(pid):
            # 进程已死亡，清除死锁
            LOCK_FILE.unlink(missing_ok=True)
            return None

        # 检查是否超时（防卡死自愈）
        lock_ts = data.get("timestamp") or 0
        ttl = data.get("ttl_seconds") or DEFAULT_LOCK_TTL_SECONDS
        if lock_ts > 0 and (time.time() - lock_ts) > ttl:
            # 超过 TTL 未刷新心跳，判定为卡死，自动自愈清除
            LOCK_FILE.unlink(missing_ok=True)
            return None

        if check_collecting_only:
            phase = data.get("phase", "collecting")
            pause_requested = data.get("pause_requested", True)
            if phase != "collecting" and not pause_requested:
                return None

        return data
    except Exception:
        return None


def is_priority_collecting() -> bool:
    """判断当前是否有高优先级自动任务正在进行核心数据采集"""
    info = get_running_lock_info(check_collecting_only=True)
    return info is not None


def wait_for_priority_task(logger=None, poll_interval: float = 2.0, context: str = "") -> bool:
    """
    在长周期手动任务（如 batch_scan / reapply_rules / collect / 后台手动任务）的关键执行间隙调用。
    规则：
    - 若自身就是持锁进程，直接放行。
    - 若检测到高优先级自动任务正在采集，主动暂停挂起并轮询，直到自动任务完成本阶段采集后唤醒。
    返回 True 表示曾发生过让道等待；返回 False 表示一路通畅无需等待。
    """
    global _CURRENT_PROCESS_HOLDS_LOCK
    if _CURRENT_PROCESS_HOLDS_LOCK:
        return False

    lock_info = get_running_lock_info(check_collecting_only=True)
    if not lock_info:
        return False

    waited = False
    while lock_info:
        if not waited:
            task_desc = lock_info.get("task_name", "定时日更监控")
            started_at = lock_info.get("started_at", "")
            pid = lock_info.get("pid")
            ctx_desc = f" [{context}]" if context else ""
            msg = (
                f"⏳ [优先级避让]{ctx_desc} 检测到自动定时任务【{task_desc}】(PID {pid}, 启动于 {started_at}) "
                "正在采集，手动任务已主动暂停挂起，等待自动任务完成本阶段采集..."
            )
            if logger and hasattr(logger, "info"):
                logger.info(msg)
            else:
                print(msg, flush=True)
            waited = True

        time.sleep(poll_interval)
        lock_info = get_running_lock_info(check_collecting_only=True)

    if waited:
        msg = "▶️ [避让恢复] 自动定时任务本阶段采集已完成，手动任务恢复执行！"
        if logger and hasattr(logger, "info"):
            logger.info(msg)
        else:
            print(msg, flush=True)

    return True

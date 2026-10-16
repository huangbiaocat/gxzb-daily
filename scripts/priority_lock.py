# -*- coding: utf-8 -*-
"""高优先级任务锁与协作式让道避让机制。

核心目标：
1. 定时日更任务（run_daily.py）声明高优先级锁；
2. 手动长周期批量任务（batch_scan.py、reapply_rules.py）在每完成一天的间隙主动检查该锁；
3. 若定时任务正在运行，手动任务主动挂起待命（释放 SQLite 连接与系统资源），避免并发写锁竞争与 OOM；
4. 定时任务完成后释放锁，手动任务毫秒级自动唤醒并继续，全程无须人工干预。
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
    """高优先级任务运行期守护锁（支持 ContextManager）"""
    def __init__(self, task_name: str = "定时日更任务", details: Optional[Dict[str, Any]] = None):
        self.task_name = task_name
        self.details = details or {}
        self.acquired = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()

    def acquire(self):
        try:
            LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
            info = {
                "pid": os.getpid(),
                "task_name": self.task_name,
                "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "details": self.details,
            }
            LOCK_FILE.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
            self.acquired = True
            atexit.register(self.release)
        except Exception as e:
            pass

    def release(self):
        if self.acquired:
            try:
                if LOCK_FILE.exists():
                    info = get_running_lock_info()
                    if info and info.get("pid") == os.getpid():
                        LOCK_FILE.unlink(missing_ok=True)
                    elif not info:
                        LOCK_FILE.unlink(missing_ok=True)
            except Exception:
                pass
            self.acquired = False


def get_running_lock_info() -> Optional[Dict[str, Any]]:
    """读取当前正在持有高优先级锁的信息。若对应进程已死，自动清理过期僵尸锁。"""
    if not LOCK_FILE.exists():
        return None
    try:
        data = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
        pid = data.get("pid")
        if pid and is_process_alive(pid):
            return data
        else:
            # 进程已死亡，清除僵尸锁
            LOCK_FILE.unlink(missing_ok=True)
            return None
    except Exception:
        return None


def wait_for_priority_task(logger=None, poll_interval: float = 3.0) -> bool:
    """
    在长周期手动任务（如 batch_scan / reapply_rules）的迭代间隙调用。
    若检测到高优先级定时任务运行，主动挂起待命并轮询，直到高优先级任务退出后唤醒。
    返回 True 表示曾发生过让道等待；返回 False 表示一路通畅无需等待。
    """
    lock_info = get_running_lock_info()
    if not lock_info:
        return False

    waited = False
    while lock_info:
        if not waited:
            task_desc = lock_info.get("task_name", "定时日更任务")
            started_at = lock_info.get("started_at", "")
            pid = lock_info.get("pid")
            msg = (
                f"⏳ [优先级避让] 检测到高优先级【{task_desc}】(PID {pid}, 启动时间 {started_at}) "
                "正在运行，手动任务主动挂起避让，等待定时任务完成..."
            )
            if logger and hasattr(logger, "info"):
                logger.info(msg)
            else:
                print(msg, flush=True)
            waited = True

        time.sleep(poll_interval)
        lock_info = get_running_lock_info()

    if waited:
        msg = "▶️ [避让恢复] 高优先级定时任务已完成，手动任务自动恢复运行！"
        if logger and hasattr(logger, "info"):
            logger.info(msg)
        else:
            print(msg, flush=True)

    return True

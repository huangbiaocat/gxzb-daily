# -*- coding: utf-8 -*-
import unittest
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestDailyPageOvertime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 构建 2026-09-22 页面
        res = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "build_daily_page.py"), "--date", "2026-09-22", "--no-vps"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT)
        )
        if res.returncode != 0:
            raise RuntimeError(f"build_daily_page failed:\n{res.stdout}\n{res.stderr}")

    def test_overtime_capsule_and_button_present(self):
        html_path = REPO_ROOT / "dist" / "2026-09-22.html"
        self.assertTrue(html_path.exists())
        content = html_path.read_text(encoding="utf-8")

        # 检查样式
        self.assertIn(".overtime-chip", content)
        self.assertIn(".btn-overtime", content)

        # 检查筛选按钮
        self.assertIn('id="btnOvertimeOnly"', content)
        self.assertIn('id="overtimeCount">22<', content)
        self.assertIn("仅看加班发布", content)

        # 检查渲染脚本中对 overtime-chip 与 data-overtime 的支持
        self.assertIn("data-overtime", content)
        self.assertIn("overtime-chip", content)
        self.assertIn("加班发布", content)
        self.assertIn("overtimeOnly", content)


if __name__ == "__main__":
    unittest.main()

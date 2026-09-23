# -*- coding: utf-8 -*-
import unittest
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestDailyPageFocusCard(unittest.TestCase):
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

    def test_focus_stat_card_present(self):
        html_path = REPO_ROOT / "dist" / "2026-09-22.html"
        self.assertTrue(html_path.exists())
        content = html_path.read_text(encoding="utf-8")

        # 检查 7 列栅格样式与重点卡片样式
        self.assertIn(".stats-grid.cols-7", content)
        self.assertIn(".stat-box.stage-focus", content)
        self.assertIn(".stat-box.stage-focus .sb-label { color: #dc2626 !important; }", content)

        # 检查重点信息指标卡 HTML 结构
        self.assertIn('class="stat-box stage-focus clickable" data-stage="__focus__" title="点击筛选 重点信息"', content)
        self.assertIn('<div class="sb-label">重点信息</div>', content)
        self.assertIn('id="stat-focus">3<span class="sb-unit">条</span>', content)

        # 检查交互联动与计数脚本
        self.assertIn("targetStage === '__focus__'", content)
        self.assertIn("statFocusEl", content)


if __name__ == "__main__":
    unittest.main()

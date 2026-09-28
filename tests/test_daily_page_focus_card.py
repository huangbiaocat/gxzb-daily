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

        # 检查 8 列栅格样式与重点卡片样式
        self.assertIn(".stats-grid.cols-8", content)
        self.assertIn(".stat-box.stage-focus-project", content)
        self.assertIn(".stat-box.stage-focus-owner", content)

        # 检查重点项目与重点业主指标卡 HTML 结构
        self.assertIn('class="stat-box stage-focus-project clickable" data-stage="__focus_project__" title="点击筛选 重点项目"', content)
        self.assertIn('class="stat-box stage-focus-owner clickable" data-stage="__focus_owner__" title="点击筛选 重点业主"', content)
        self.assertIn('id="stat-focus-project"', content)
        self.assertIn('id="stat-focus-owner"', content)

        # 检查交互联动
        self.assertIn("targetStage === '__focus_project__'", content)
        self.assertIn("targetStage === '__focus_owner__'", content)


if __name__ == "__main__":
    unittest.main()

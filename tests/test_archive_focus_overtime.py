import unittest
import json
import subprocess
import sys
from pathlib import Path
import config

ROOT = Path(__file__).resolve().parent.parent

class TestArchiveFocusOvertime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cmd = [
            sys.executable,
            str(ROOT / "scripts" / "build_archive_page.py"),
            "--no-vps"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
        assert res.returncode == 0, f"build_archive_page failed: {res.stderr}"

    def test_archive_json_has_counts(self):
        arc_file = config.DATA_DIR / "archive.json"
        self.assertTrue(arc_file.exists())
        data = json.loads(arc_file.read_text(encoding="utf-8"))
        self.assertIn("total_focus", data)
        self.assertIn("total_overtime", data)
        self.assertGreater(data["total_focus"], 0)
        self.assertGreater(data["total_overtime"], 0)

        for d in data.get("days", []):
            self.assertIn("focus_count", d)
            self.assertIn("overtime_count", d)
            self.assertIsInstance(d["focus_count"], int)
            self.assertIsInstance(d["overtime_count"], int)

    def test_calendar_view_displays_counts(self):
        index_html = (config.SITE_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-date="2026-09-22"', index_html)
        self.assertIn('data-focus="3"', index_html)
        self.assertNotIn('⚡', index_html)
        self.assertIn('重点 3', index_html)
        # 首页不显示加班发布数量
        self.assertNotIn('🌙', index_html)
        self.assertNotIn('加班 22', index_html)

    def test_list_view_displays_counts(self):
        index_html = (config.SITE_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn('class="chip-alert"', index_html)
        self.assertIn('当日重点信息 3 条', index_html)
        # 首页不显示加班发布数量
        self.assertNotIn('class="chip-nonwork"', index_html)
        self.assertNotIn('当日加班发布', index_html)

    def test_month_stats_group(self):
        index_html = (config.SITE_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn('alert-pill', index_html)
        self.assertIn('重点信息:', index_html)
        # 首页不显示加班发布数量
        self.assertNotIn('nonwork-pill', index_html)
        self.assertNotIn('加班发布:', index_html)

    def test_archive_hint_does_not_display_overtime(self):
        index_html = (config.SITE_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn('重点信息', index_html)
        self.assertNotIn('加班发布', index_html)

if __name__ == "__main__":
    unittest.main()

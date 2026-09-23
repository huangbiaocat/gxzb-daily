import unittest
import json
import subprocess
import sys
from pathlib import Path
import config

ROOT = Path(__file__).resolve().parent.parent

class TestFooterTime(unittest.TestCase):
    def test_daily_page_footer_with_data(self):
        cmd = [
            sys.executable,
            str(ROOT / "scripts" / "build_daily_page.py"),
            "--date", "2026-09-21",
            "--no-vps",
            "--scan-time", "2026-09-23 16:00:00"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(res.returncode, 0, msg=f"build_daily_page failed: {res.stderr}")

        data_file = config.DATA_DIR / "daily" / "2026-09-21.json"
        items = json.loads(data_file.read_text(encoding="utf-8")) if data_file.exists() else []
        valid_pubs = [it.get("pub_time") for it in items if it.get("pub_time")]
        expected_pub = max(valid_pubs) if valid_pubs else "暂无"

        out_html = (config.SITE_DIR / "2026-09-21.html").read_text(encoding="utf-8")
        self.assertIn(f"最新公告时间：{expected_pub}", out_html)
        self.assertIn("最近扫描时间：2026-09-23 16:00:00", out_html)
        self.assertIn("由自动化采集监控系统", out_html)

    def test_daily_page_finalized_seal_time(self):
        cmd = [
            sys.executable,
            str(ROOT / "scripts" / "build_daily_page.py"),
            "--date", "2026-09-20",
            "--no-vps"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(res.returncode, 0, msg=f"build_daily_page failed: {res.stderr}")

        out_html = (config.SITE_DIR / "2026-09-20.html").read_text(encoding="utf-8")
        self.assertIn("最新公告时间：2026-09-20 22:12:04", out_html)
        self.assertIn("最近扫描时间：2026-09-21 21:46:29", out_html)

    def test_archive_page_footer(self):
        cmd = [
            sys.executable,
            str(ROOT / "scripts" / "build_archive_page.py"),
            "--no-vps"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(res.returncode, 0, msg=f"build_archive_page failed: {res.stderr}")

        out_html = (config.SITE_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn("最近扫描时间：", out_html)
        self.assertIn("由自动化采集监控系统", out_html)

if __name__ == "__main__":
    unittest.main()

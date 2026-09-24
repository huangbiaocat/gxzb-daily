import unittest
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestDailyPageCapsuleFilters(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 生成 2026-09-23 页面进行验证
        cmd = [
            sys.executable,
            str(ROOT / "scripts" / "build_daily_page.py"),
            "--date", "2026-09-23",
            "--no-vps"
        ]
        res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        assert res.returncode == 0, f"build_daily_page failed:\n{res.stderr}\n{res.stdout}"
        cls.html_path = ROOT / "dist" / "2026-09-23.html"
        cls.content = cls.html_path.read_text(encoding="utf-8")

    def test_css_active_and_hover_styles(self):
        """验证 CSS 中包含胶囊点击、激活与悬浮样式"""
        self.assertIn(".cat-block .type-head.active-filter", self.content)
        self.assertIn(".cat-block .notice-item .city-tag.active-filter", self.content)
        self.assertIn(".cat-block .notice-item .source-tag.active-filter", self.content)
        self.assertIn(".cat-header .cat-chip.active-filter", self.content)
        self.assertIn(".focus-chip.active-filter", self.content)
        self.assertIn("cursor: pointer", self.content)

    def test_html_capsule_data_attributes(self):
        """验证生成的 HTML 胶囊包含正确的 data-filter 属性与交互提示 title"""
        # 环节胶囊
        self.assertIn('data-filter-stage="', self.content)
        self.assertIn('title="点击筛选【', self.content)
        # 地市胶囊
        self.assertIn('data-filter-city="', self.content)
        # 信源胶囊
        self.assertIn('data-filter-source="崇左阳光采购"', self.content)
        # 大类胶囊
        self.assertIn('data-filter-cat="', self.content)

    def test_js_filter_interaction_logic(self):
        """验证 JavaScript 包含胶囊事件委托、城市映射与 Toast 提示逻辑"""
        self.assertIn("function mapToCityFilter(raw)", self.content)
        self.assertIn("function showFilterToast(msg)", self.content)
        self.assertIn("function checkScrollAfterFilter()", self.content)
        self.assertIn("container.addEventListener('click', function", self.content)
        self.assertIn("typeHead.getAttribute('data-filter-stage')", self.content)
        self.assertIn("sourceTag.getAttribute('data-filter-source')", self.content)
        self.assertIn("cityTag.getAttribute('data-filter-city')", self.content)
        self.assertIn("e.preventDefault()", self.content)
        self.assertIn("e.stopPropagation()", self.content)


if __name__ == "__main__":
    unittest.main()

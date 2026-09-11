# -*- coding: utf-8 -*-
"""extractors 抽取规则回归测试（纯标准库，无需 pytest）。

运行：python3 tests/test_rules.py
覆盖：金额标准化 / 日期标准化 / 机构名判定 / 标段名过滤 / 联合体拆解 / 字段抽取，
每个用例都对应一次线上清洗中修掉的真实误报（注释里给出触发原文）。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "extractors"))

import normalize as nz  # noqa: E402
import rules as rl      # noqa: E402


class TestMoney(unittest.TestCase):
    """金额统一到元；费率型保留百分比不换算。"""

    def test_plain_yuan(self):
        r = nz.parse_money("11136752.54元")
        self.assertEqual(r["value"], 11136752.54)
        self.assertEqual(r["kind"], "yuan")

    def test_wan(self):
        r = nz.parse_money("237.96万元")
        self.assertAlmostEqual(r["value"], 2379600.0, places=2)

    def test_yi(self):
        r = nz.parse_money("1.67亿元")
        self.assertAlmostEqual(r["value"], 167000000.0, places=2)

    def test_thousand(self):
        r = nz.parse_money("叁仟万元整")
        self.assertAlmostEqual(r["value"], 30000000.0, places=2)

    def test_chinese_capital_with_yuan_marker(self):
        """大写汉字金额 + 中文单位，不得再按 default_unit 二次放大。"""
        r = nz.parse_money("壹佰玖拾肆万零陆佰陆拾柒元肆角整")
        self.assertAlmostEqual(r["value"], 1940667.0, places=1)

    def test_currency_symbol_yuan(self):
        r = nz.parse_money("¥5130000.00元")
        self.assertAlmostEqual(r["value"], 5130000.0, places=2)

    def test_rate_keeps_percent(self):
        r = nz.parse_money("99.60%")
        self.assertAlmostEqual(r["value"], 99.6, places=2)
        self.assertEqual(r["kind"], "rate")

    def test_clause_number_is_not_money(self):
        """条款编号 "2.2.2" 不是金额（曾抽出 2.20 元的假控制价）。"""
        self.assertIsNone(nz.parse_money("2.2.2")["value"])
        self.assertIsNone(nz.parse_money("按本表2.2.2内容计算评标基准值(A)。")["value"])

    def test_bare_number_needs_inference_flag(self):
        r = nz.parse_money("1234567")
        self.assertAlmostEqual(r["value"], 1234567.0, places=2)
        self.assertTrue(r["unit_inferred"])


class TestDate(unittest.TestCase):
    def test_chinese_date(self):
        self.assertEqual(nz.parse_date("2026年9月10日 09:30"), "2026-09-10 09:30")

    def test_iso_date(self):
        self.assertEqual(nz.parse_date("2026-09-10"), "2026-09-10 00:00")

    def test_empty(self):
        self.assertFalse(nz.parse_date("无"))


class TestOrg(unittest.TestCase):
    def test_company_ok(self):
        self.assertTrue(rl.valid_org_field("平果鼎晟建设工程有限公司"))

    def test_government_ok(self):
        self.assertTrue(rl.valid_org_field("广西壮族自治区政府"))
        self.assertTrue(rl.valid_org_field("田东县教育局"))

    def test_school_ok(self):
        self.assertTrue(rl.valid_org_field("广西大学"))

    def test_sentence_with_suo_not_org(self):
        """“所有标段”里的“所”曾把整句误判为机构名。"""
        self.assertFalse(nz.looks_like_org("各投标人可就招标项目的所有标段进行投标"))
        self.assertFalse(rl.valid_org_field("各投标人可就招标项目的所有标段进行投标"))

    def test_suffix_and_alias_clean(self):
        """法定后缀之外的解释性尾巴要砍掉；机构名本身的括号别名要去掉。"""
        self.assertEqual(nz.truncate_at_suffix("广西建工集团有限责任公司（以下简称甲方）"),
                         "广西建工集团有限责任公司")
        self.assertEqual(nz.strip_alias_paren("广西北港规划设计院有限公司(原南宁设计院)"),
                         "广西北港规划设计院有限公司")


class TestLots(unittest.TestCase):
    """标段名只留真名，说明性长句/条款不进入。"""

    SAMPLE = (
        "3. 投标人资格要求\n"
        "3.1 各投标人可就招标项目的所有标段进行投标，并允许中标其中所有标段。\n"
        "标段(包)编号:E4507002846007640001001\n"
        "标段划分：本项目划分为1个标段\n"
        "南崇铁路工程CCSCSG标段、CCSCSG标段\n"
    )

    def test_no_clause_noise(self):
        lots = rl.extract_lots(self.SAMPLE)
        for bad in ("招标项", "本标段", "各标段"):
            self.assertNotIn(bad, lots)
        joined = "".join(lots)
        for token in ("应当", "根据", "否则", "同一人", "编号", "名称"):
            self.assertNotIn(token, joined)

    def test_keeps_real_lot(self):
        lots = rl.extract_lots(self.SAMPLE)
        self.assertTrue(any("CCSCSG标段" in x for x in lots), lots)

    def test_tidy_lot_removes_whitespace_and_tail_digit(self):
        self.assertEqual(rl._tidy_lot("CCSCSG标段\n4"), "CCSCSG标段")


class TestMembers(unittest.TestCase):
    def test_split_by_pause_mark(self):
        names = rl.extract_members("联合体成员：广西壮族自治区建筑科学研究设计院、平果鼎晟建设工程有限公司。")
        self.assertIn("广西壮族自治区建筑科学研究设计院", names)
        self.assertIn("平果鼎晟建设工程有限公司", names)

    def test_label_prefix_stripped_and_sentence_dropped(self):
        names = rl.extract_members(
            "联合体投标的成员单位:华航联合建筑(广西)有限公司；各投标人可就招标项目的所有标段进行投标。")
        self.assertIn("华航联合建筑(广西)有限公司", names)
        for n in names:
            self.assertFalse(n.startswith("成员单位"))
            self.assertNotIn("投标人可就", n)


class TestQualification(unittest.TestCase):
    def test_tail_fields_cut(self):
        text = ("资质要求\n3.1 本次招标要求投标人须已办理“桂建云”入库手续并处于有效状态,"
                "具备建筑工程施工总承包三级(含)以上资质 【备注:1.招标人可根据…】")
        val = rl.extract_qualification(text)
        self.assertTrue(val.startswith("具备"), val)
        self.assertNotIn("备注", val)
        self.assertNotIn("【", val)


class TestP1Regressions(unittest.TestCase):
    """P1 实跑（2026-09-11 批次）暴露的 4 处缺陷回归。"""

    def test_money_fragment_with_formula_is_rejected(self):
        # "…发包人公布的最高投标限价的1.5%计算。" 曾把 1.5% 误判为控制价
        self.assertFalse(rl._money_cell_ok("的1.5%计算。"))
        self.assertFalse(rl._money_cell_ok("按合同价的2%计取。"))

    def test_money_capital_amount_with_parenthesis_kept(self):
        # 大写金额 + 括号阿拉伯数字合计 40+ 字，旧上限 40 会误杀有效控制价
        cell = "为（人民币）：陆佰叁拾贰万壹仟贰佰肆拾柒元贰角叁分（¥6321247.23）；"
        self.assertTrue(rl._money_cell_ok(cell))
        self.assertEqual(nz.parse_money(cell)["value"], 6321247.23)

    def test_template_money_cell_still_rejected(self):
        self.assertFalse(rl._money_cell_ok("【或每平方米 元;或投标费率 %;或其他报价方式: 】"))

    def test_rating_formula_and_void_notice_rejected(self):
        # 评标办法脚注 / 废标公告句式，不含任何金额语义
        self.assertFalse(rl._money_cell_ok("(Bn)时,Bn-S<0的,每(-1%)值扣[A2]分。"))
        self.assertFalse(rl._money_cell_ok(
            "x(1-1%),评标委员会认定所有投标人的投标文件均不通过形式评审(商务标),故做否决投标处理,项目招标失败。"))
        # 合法大写+小写金额不得被误伤
        self.assertTrue(rl._money_cell_ok("为(人民币):陆佰叁拾贰万壹仟贰佰肆拾柒元贰角叁分(¥6321247.23);"))

    def test_project_name_leading_year_stripped(self):
        # 正文「项目名称」带年份前缀
        text = "项目名称\n2026年星岛湖镇洪潮村迁安桥等4个危桥改造项目\n项目招标编号\nE4500001"
        self.assertEqual(rl.extract_project_name(text, ""),
                         "星岛湖镇洪潮村迁安桥等4个危桥改造项目")
        # 标题回退路径：年份不得残留（旧实现会留下"年星岛湖…"）
        title = "2026年星岛湖镇洪潮村迁安桥等4个危桥改造项目中标候选人公示更正公告一"
        val = rl.extract_project_name("", title)
        self.assertFalse(val.startswith("年"), val)
        self.assertTrue(val.startswith("星岛湖镇洪潮村迁安桥等4个危桥改造项目"), val)
        # 序号型前导仍需剥离，且不得误伤正常名称
        self.assertEqual(rl.strip_leading_numbering("（1）某某水库除险加固工程"), "某某水库除险加固工程")
        self.assertEqual(rl.strip_leading_numbering("西江航运干线航道整治工程"), "西江航运干线航道整治工程")

    def test_qualification_inside_parenthesis_not_hit(self):
        text = ("投标所用企业业绩、企业诚信综合评价分（包括资格要求和加分业绩、诚信综合评价分）\n"
                "企业业绩：/\n单位资质\n建筑工程施工总承包二级")
        self.assertEqual(rl.extract_qualification(text), "")

    def test_qualification_normal_line_still_hit(self):
        text = ("资格要求\n具备市政公用工程施工总承包三级及以上资质，并在人员、设备、资金等方面具备相应的施工能力。\n"
                "备注：本项目不接受联合体投标。")
        val = rl.extract_qualification(text)
        self.assertTrue(val.startswith("具备"), val)
        self.assertNotIn("备注", val)

    def test_scale_cut_at_following_field(self):
        text = ("建设内容与规模\n本项目包括道路工程、给排水工程等。\n开标时间\n2026年9月10日\n"
                "开标地点\n北海市公共资源交易中心")
        val = rl.extract_scale(text)
        self.assertIn("道路工程", val)
        self.assertNotIn("开标时间", val)
        self.assertNotIn("北海市公共资源交易中心", val)


if __name__ == "__main__":
    unittest.main(verbosity=2)

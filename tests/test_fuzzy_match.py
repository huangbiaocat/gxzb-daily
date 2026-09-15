# -*- coding: utf-8 -*-
"""重点跟踪项目/业主/类型 模糊匹配单元测试。

运行：python3 tests/test_fuzzy_match.py
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config  # noqa: E402


class TestFuzzyMatch(unittest.TestCase):
    def test_exact_substring(self):
        self.assertTrue(config.fuzzy_match("平陆运河", "平陆运河青年枢纽工程施工招标"))
        self.assertTrue(config.fuzzy_match_project("平陆运河", "平陆运河青年枢纽工程施工招标"))

    def test_gap_tolerance(self):
        # 官方标题中间插入“广西”、“B2标段”
        self.assertTrue(config.fuzzy_match(
            "环北部湾水资源配置工程",
            "关于环北部湾广西水资源配置工程B2标段施工招标公告"
        ))
        # 跨度过大时不应当误报
        self.assertFalse(config.fuzzy_match(
            "南宁中医院",
            "南宁" + "这是一段非常长超过二十字的其他文字描述毫无关联的内容" + "中医院"
        ))

    def test_alias_and_synonyms(self):
        # 中医院 <-> 中医医院
        self.assertTrue(config.fuzzy_match(
            "南宁市中医院新院区",
            "南宁市中医医院江南新院区项目招标公告"
        ))
        # 交投 <-> 交通投资
        self.assertTrue(config.fuzzy_match("广西交投", "广西交通投资集团南宁分公司"))
        # 北投 <-> 北部湾投资
        self.assertTrue(config.fuzzy_match("广西北投", "广西北部湾投资集团有限公司"))
        # 住建局 <-> 住房和城乡建设局
        self.assertTrue(config.fuzzy_match("南宁住建局", "南宁市住房和城乡建设局"))

    def test_multi_word_and(self):
        # 空格分隔多词同时命中
        self.assertTrue(config.fuzzy_match(
            "环北部湾 水资源",
            "关于环北部湾广西水资源配置工程B2标段施工招标公告"
        ))
        self.assertTrue(config.fuzzy_match(
            "南宁+中医院",
            "南宁市中医医院江南新院区项目招标公告"
        ))
        self.assertFalse(config.fuzzy_match(
            "桂林 水资源",
            "关于环北部湾广西水资源配置工程B2标段施工招标公告"
        ))

    def test_wildcard_and_or(self):
        # 通配符 *
        self.assertTrue(config.fuzzy_match(
            "平陆*青年枢纽",
            "平陆运河青年枢纽工程施工招标"
        ))
        # 逻辑或 |
        self.assertTrue(config.fuzzy_match(
            "平陆运河|水资源工程",
            "关于环北部湾广西水资源配置工程B2标段施工招标公告"
        ))

    def test_punctuation_and_bracket_normalization(self):
        self.assertTrue(config.fuzzy_match(
            "南宁市中医院(新院区)",
            "南宁市中医院新院区（一期）工程"
        ))
        self.assertTrue(config.fuzzy_match(
            "灵剑溪排污通道治理工程（一期）",
            "桂林市灵剑溪排污通道治理工程(一期)中标结果公告"
        ))

    def test_no_false_positives(self):
        # 第一人民医院不应误判为中医院
        self.assertFalse(config.fuzzy_match(
            "南宁市中医院新院区",
            "南宁市第一人民医院新院区工程"
        ))
        self.assertFalse(config.fuzzy_match(
            "平陆运河",
            "桂林市灵剑溪排污通道治理工程"
        ))


if __name__ == "__main__":
    unittest.main(verbosity=2)

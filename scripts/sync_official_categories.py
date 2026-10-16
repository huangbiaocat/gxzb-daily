# -*- coding: utf-8 -*-
"""
从广西壮族自治区公共资源交易平台官方接口抓取并同步权威栏目层级分类树。
杜绝人工盲猜与硬编码错误。
"""

import json
import os
import sys
import requests

API_URL = "http://ggzy.jgswj.gxzf.gov.cn/EWB-FRONT/rest/GgSearchAction/getCategoryList"
SITE_GUID = "7eb5f7f1-9041-43ad-8e13-8fcb82ea831a"
HEADERS = {"Content-Type": "application/x-www-form-urlencoded"}

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, "data")
OUTPUT_FILE = os.path.join(DATA_DIR, "official_categories.json")


def fetch_children(category_num):
    payload = {"params": json.dumps({"siteGuid": SITE_GUID, "categoryNum": category_num})}
    try:
        resp = requests.post(API_URL, data=payload, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"Error fetching categoryNum={category_num}: {e}", file=sys.stderr)
    return []


def sync_taxonomy():
    print(f"正在从官方接口拉取完整分类树: {API_URL} ...")
    tree = {
        "metadata": {
            "source": "广西壮族自治区公共资源交易平台",
            "api": API_URL,
            "siteGuid": SITE_GUID,
        },
        "categories": {},
        "engineering_industries": {},
        "engineering_stages": {},
    }

    # 拉取 001001 (工程建设) 下所有子栏目
    gcjs_items = fetch_children("001001")
    if not gcjs_items:
        print("未获取到工程建设子栏目，退出", file=sys.stderr)
        return False

    for ind in gcjs_items:
        raw_code = ind.get("categorynum", "")
        code = raw_code.lstrip("a")  # e.g. 001001001
        name = ind.get("categoryname", "").strip()
        path = ind.get("path", "").strip()
        
        # 行业级别：取第 7~9 位
        ind_code = code[6:9]
        tree["engineering_industries"][ind_code] = {
            "code": ind_code,
            "full_code": code,
            "name": name,
            "path": path,
        }
        tree["categories"][code] = {"name": name, "path": path, "parent": "001001"}

        # 拉取该行业下的所有业务环节
        stage_items = fetch_children(code)
        for stg in stage_items:
            s_raw = stg.get("categorynum", "")
            s_code = s_raw.lstrip("a")  # e.g. 001001001006
            s_name = stg.get("categoryname", "").strip()
            s_path = stg.get("path", "").strip()
            
            # 环节级别：取末 3 位
            stage_code = s_code[-3:]
            tree["engineering_stages"][stage_code] = {
                "code": stage_code,
                "name": s_name,
            }
            tree["categories"][s_code] = {"name": s_name, "path": s_path, "parent": code}

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(tree, f, ensure_ascii=False, indent=2)

    print(f"官方分类同步成功 -> {OUTPUT_FILE}")
    print(f"  行业数量: {len(tree['engineering_industries'])}")
    for k, v in tree["engineering_industries"].items():
        print(f"    - [{k}] {v['name']} ({v['full_code']})")
    print(f"  环节数量: {len(tree['engineering_stages'])}")
    for k, v in tree["engineering_stages"].items():
        print(f"    - [{k}] {v['name']}")
    return True


if __name__ == "__main__":
    success = sync_taxonomy()
    sys.exit(0 if success else 1)

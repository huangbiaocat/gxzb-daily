# -*- coding: utf-8 -*-
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent


class TestBatchScanCLI(unittest.TestCase):
    def test_batch_scan_imports(self):
        """验证 batch_scan 模块导入及 os 等全局依赖正常"""
        import scripts.batch_scan as bs
        self.assertTrue(hasattr(bs, "main"))

    def test_batch_scan_event_id_setup(self):
        """验证启动关联 event_id 任务时不会抛出 NameError: name 'os' is not defined"""
        cmd = [
            sys.executable,
            str(ROOT_DIR / "scripts" / "batch_scan.py"),
            "--event-id", "evt_nonexistent_test_123",
            "--resume"
        ]
        res = subprocess.run(cmd, cwd=str(ROOT_DIR), capture_output=True, text=True, timeout=15)
        self.assertNotIn("NameError", res.stderr)
        self.assertNotIn("name 'os' is not defined", res.stderr)

    def test_all_scripts_standard_imports(self):
        """静态检查 scripts/ 下的脚本无未导入的常用标准库模块"""
        import ast
        import builtins

        scripts_dir = ROOT_DIR / "scripts"
        builtin_names = set(dir(builtins))
        checked_modules = ['os', 'sys', 'json', 'time', 're', 'subprocess', 'datetime', 'math', 'shutil', 'logging', 'argparse', 'urllib']

        for py_file in scripts_dir.glob("*.py"):
            with open(py_file, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=str(py_file))

            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for n in node.names:
                        imported.add(n.asname or n.name.split('.')[0])
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        imported.add(node.module.split('.')[0])
                    for n in node.names:
                        imported.add(n.asname or n.name)

            loaded = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                    loaded.add(node.id)

            for mod in checked_modules:
                if mod in loaded and mod not in imported and mod not in builtin_names:
                    is_local = False
                    for n in ast.walk(tree):
                        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == mod:
                            is_local = True
                            break
                        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Param)) and n.id == mod:
                            is_local = True
                            break
                    self.assertTrue(is_local, f"脚本 {py_file.name} 缺少标准模块导入: {mod}")


if __name__ == "__main__":
    unittest.main()

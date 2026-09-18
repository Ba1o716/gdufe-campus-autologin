"""打包配置的“契约测试”。

只检查配置文本，不需要联网、不需要 PyInstaller：
  * 必须是文件夹模式（onedir）——单文件模式在“每 5 分钟自愈检查”下开销太大
  * 托盘图标必须被打进包里（历史上这里出过 bug：打包后托盘变成系统默认图标）
  * 打包脚本要给出正确产物路径
"""

from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPEC = PROJECT_ROOT / "CampusLogin.spec"
BUILD_BAT = PROJECT_ROOT / "build_exe.bat"
LICENSE = PROJECT_ROOT / "LICENSE"


class PackagingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = SPEC.read_text(encoding="utf-8")

    def test_spec_is_valid_python(self):
        import ast

        ast.parse(self.spec)   # 语法错误会直接抛异常

    def test_spec_builds_a_folder_not_a_single_file(self):
        self.assertIn("COLLECT(", self.spec)
        code_lines = [
            line for line in self.spec.splitlines()
            if line.strip().startswith("exclude_binaries=True")
        ]
        self.assertEqual(len(code_lines), 2, "两个 exe 都要走文件夹模式")
        self.assertNotIn("a.datas,\n    [],", self.spec)

    def test_tray_icon_is_bundled_for_both_layouts(self):
        # icon_path() 会在 assets/ 和 campus_login/assets/ 两处查找，包里两处都要有
        self.assertIn('(str(icon), "assets")', self.spec)
        self.assertIn('(str(icon), "campus_login/assets")', self.spec)

    def test_both_exes_are_collected_into_one_folder(self):
        self.assertIn('name="CampusLogin"', self.spec)
        self.assertIn('name="CampusLoginCLI"', self.spec)
        self.assertIn("console=False", self.spec)   # 托盘版无控制台
        self.assertIn("console=True", self.spec)    # CLI 版带控制台

    def test_build_script_points_to_folder_output(self):
        text = BUILD_BAT.read_text(encoding="ascii")   # 纯 ASCII，避免中文乱码
        self.assertIn(r"dist\CampusLogin", text.replace("\\\\", "\\"))
        self.assertIn("PyInstaller", text)
        self.assertIn("Compress-Archive", text)        # 自动压成 zip 方便分发

    def test_batch_files_are_pure_ascii(self):
        """中文版 Windows 的 cmd 会按 GBK 解析 .bat，含中文会乱码甚至执行失败。"""
        for name in (
            "build_exe.bat",
            "install.bat",
            "uninstall.bat",
            "run_tray.bat",
            "selftest.bat",
            "run_tests.bat",
        ):
            data = (PROJECT_ROOT / name).read_bytes()
            self.assertEqual(
                [byte for byte in data if byte > 127], [], f"{name} 必须是纯 ASCII"
            )

    def test_license_has_copyright_holder(self):
        text = LICENSE.read_text(encoding="utf-8")
        self.assertIn("MIT License", text)
        self.assertIn("Ba1o716", text)
        self.assertNotIn("<把你的名字", text)


if __name__ == "__main__":
    unittest.main()

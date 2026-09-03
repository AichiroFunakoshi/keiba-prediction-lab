import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "open-ui-demo.command"
APP_BUILDER = ROOT / "build-raceweave-app.command"


class MacOsUiLauncherTest(unittest.TestCase):
    def test_launcher_is_executable_and_has_valid_bash_syntax(self) -> None:
        result = subprocess.run(
            ("/bin/bash", "-n", str(LAUNCHER)),
            check=False,
            capture_output=True,
            text=True,
        )
        index = subprocess.run(
            ("git", "ls-files", "--stage", "open-ui-demo.command"),
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertTrue(index.stdout.startswith("100755 "))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_launcher_uses_project_venv_and_synthetic_demo_commands(self) -> None:
        content = LAUNCHER.read_text(encoding="utf-8")

        self.assertIn('/.venv/bin/python"', content)
        self.assertIn("init-ui-demo", content)
        self.assertIn("serve-ui-demo", content)
        self.assertIn("合成デモ", content)

    def test_app_builder_is_valid_and_keeps_build_outputs_out_of_git(self) -> None:
        result = subprocess.run(
            ("/bin/bash", "-n", str(APP_BUILDER)),
            check=False,
            capture_output=True,
            text=True,
        )
        content = APP_BUILDER.read_text(encoding="utf-8")
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--windowed", content)
        self.assertIn("--hidden-import webview", content)
        self.assertNotIn("--collect-all webview", content)
        self.assertIn("CFBundleShortVersionString", content)
        self.assertIn("public.app-category.utilities", content)
        self.assertIn("codesign --verify --deep --strict", content)
        self.assertIn("dist/", ignore)
        self.assertIn("build/", ignore)


if __name__ == "__main__":
    unittest.main()

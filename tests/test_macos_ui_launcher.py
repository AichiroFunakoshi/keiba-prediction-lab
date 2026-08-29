import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "open-ui-demo.command"


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


if __name__ == "__main__":
    unittest.main()

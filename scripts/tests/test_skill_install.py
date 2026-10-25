"""Offline tests for the portable KB skill installer.

Every test uses temporary HOME/agent/config paths. Nothing is installed into a
real agent directory and no existing config contents are printed.
"""

from __future__ import annotations

import os
import json
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "skills" / "install.sh"


class SkillInstallerTests(unittest.TestCase):
    def run_installer(self, *args: str, root: Path) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["HOME"] = str(root / "home")
        env["CLAUDE_HOME"] = str(root / "claude")
        env["CODEX_HOME"] = str(root / "codex")
        env.pop("KB_ROOT", None)
        env.pop("KB_CONFIG", None)
        env.pop("KB_CONFIG_PATH", None)
        env.pop("KB_AGENT", None)
        return subprocess.run(
            ["bash", str(INSTALLER), *args],
            cwd=REPO,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_claude_default_is_backward_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            result = self.run_installer("--agent", "claude", root=root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((root / "claude" / "skills" / "kb-query" / "SKILL.md").is_file())
            self.assertTrue((root / "claude" / "kb-config.md").is_file())

    def test_codex_default_creates_concrete_config(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            result = self.run_installer("--agent", "codex", root=root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((root / "codex" / "skills" / "kb-query" / "SKILL.md").is_file())
            self.assertIn("KB_PERSONAL_ROOT:", (root / "codex" / "kb-config.md").read_text())

    def test_custom_destination_and_config(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            destination = root / "portable-skills"
            config = root / "runtime" / "kb-config.md"
            result = self.run_installer(
                "--agent", "custom", "--destination", str(destination),
                "--config", str(config), root=root,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((destination / "kb-query" / "SKILL.md").is_file())
            self.assertTrue((destination.parent / "kb-tools" / "kb-search").is_file())
            self.assertFalse((destination / "kb-relink").exists())
            self.assertTrue(config.is_file())
            self.assertNotIn("book-to-skill", {path.name for path in destination.iterdir()})

    def test_custom_agent_requires_destination(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            result = self.run_installer("--agent", "custom", root=Path(raw))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("requires --destination", result.stderr)

    def test_existing_config_is_preserved_and_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            destination = root / "skills"
            config = root / "config.md"
            config.parent.mkdir(parents=True, exist_ok=True)
            sentinel = "KB_ROOT: /private/sentinel/vault\nVAULT_NAME: hidden-vault\n"
            config.write_text(sentinel)
            first = self.run_installer(
                "--agent", "custom", "--destination", str(destination),
                "--config", str(config), root=root,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(config.read_text(), sentinel)
            output = first.stdout + first.stderr
            self.assertNotIn("/private/sentinel/vault", output)
            self.assertNotIn("hidden-vault", output)
            second = self.run_installer(
                "--agent", "custom", "--destination", str(destination),
                "--config", str(config), "--force", root=root,
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(config.read_text(), sentinel)
            replaced = self.run_installer(
                "--agent", "custom", "--destination", str(destination),
                "--config", str(config), "--force", "--force-config", root=root,
            )
            self.assertEqual(replaced.returncode, 0, replaced.stderr)
            self.assertNotEqual(config.read_text(), sentinel)
            self.assertIn("KB_PERSONAL_ROOT:", config.read_text())

    def test_force_replaces_skill_files_but_preserves_unrelated_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            destination = root / "skills"
            config = root / "config.md"
            first = self.run_installer(
                "--agent", "custom", "--destination", str(destination),
                "--config", str(config), root=root,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            extra = destination / "kb-query" / "local-extra.txt"
            extra.write_text("keep me")
            old_skill = destination / "kb-query" / "SKILL.md"
            old_skill.write_text("old content")
            forced = self.run_installer(
                "--agent", "custom", "--destination", str(destination),
                "--config", str(config), "--force", root=root,
            )
            self.assertEqual(forced.returncode, 0, forced.stderr)
            self.assertEqual(extra.read_text(), "keep me")
            self.assertNotEqual(old_skill.read_text(), "old content")

    def test_custom_config_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            destination = root / "skills"
            real_config_dir = root / "real-config"
            real_config_dir.mkdir()
            linked_config_dir = root / "linked-config"
            linked_config_dir.symlink_to(real_config_dir, target_is_directory=True)
            result = self.run_installer(
                "--agent", "custom", "--destination", str(destination),
                "--config", str(linked_config_dir / "kb-config.md"), root=root,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("symlink", result.stderr)
            self.assertFalse((real_config_dir / "kb-config.md").exists())


if __name__ == "__main__":
    unittest.main()


def test_installed_commands_retain_the_dependency_environment(tmp_path):
    import yaml

    environment = tmp_path / "python's environment"
    settings = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    subprocess.run([sys.executable, '-B', '-m', 'venv', '--without-pip', str(environment)], check=True, env=settings)
    interpreter = environment / 'bin/python'
    purelib = subprocess.check_output([str(interpreter), '-c', 'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True, env=settings).strip()
    shutil.copytree(Path(yaml.__file__).parent, Path(purelib) / 'yaml', ignore=shutil.ignore_patterns('__pycache__'))
    root = tmp_path / 'team'
    root.mkdir()
    (root / 'note.md').write_text('# Test\nRetained evidence.\n')
    destination, tools, config = tmp_path / 'skills', tmp_path / 'tools', tmp_path / 'roots.md'
    args = [str(REPO / 'skills/install.py'), '--agent', 'custom', '--destination', str(destination),
            '--tools-directory', str(tools), '--config', str(config)]
    subprocess.run([str(interpreter), '-B', *args, '--work-root', str(root)], check=True, capture_output=True, env=settings)
    skill = (destination / 'kb-audit/SKILL.md').read_text()
    command = shlex.split(re.search(r'```sh\n([^\n]+)', skill)[1])
    assert command[0] == str(interpreter)
    command[command.index('--domain') + 1] = 'work'
    result = subprocess.run(command, capture_output=True, text=True, env=settings)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['counts']['files'] == 1
    status = subprocess.run([sys.executable, '-B', *args, '--status'], capture_output=True, text=True, env=settings)
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)['python_executable'] == str(interpreter)

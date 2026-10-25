"""Install the explicit KB release and verify its managed files."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys

SOURCE = Path(__file__).resolve().parent
sys.path.insert(0, str(SOURCE.parent / "scripts"))
from kb_config import KBConfigError, atomic_write_text, read_regular_text


def checked(value):
    path = Path(os.path.abspath(os.path.expanduser(str(value))))
    for part in (path, *path.parents):
        if part.is_symlink():
            raise KBConfigError(f"symlink install path: {part}")
    return path


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", choices=("codex", "claude", "custom"), default="codex")
    parser.add_argument("--destination", "--dest")
    parser.add_argument("--config")
    parser.add_argument("--tools-directory")
    parser.add_argument("--personal-root")
    parser.add_argument("--work-root")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--force-config", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--status", action="store_true")
    args = parser.parse_args()
    if args.agent == "custom" and not args.destination:
        parser.error("custom agent requires --destination")
    agent_home = Path(os.environ.get(f"{args.agent.upper()}_HOME", str(Path.home() / f".{args.agent}")))
    destination = checked(args.destination or agent_home / "skills")
    config = checked(args.config or (destination / "kb-config.md" if args.agent == "custom" else agent_home / "kb-config.md"))
    tools = checked(args.tools_directory or (destination.parent / "kb-tools"))
    if destination == tools or destination in tools.parents or tools in destination.parents:
        raise KBConfigError("tools and skills must have separate directories")
    release = json.loads(read_regular_text(SOURCE / "release.json"))
    manifest_path = checked(destination / ".kb-release.json")
    previous = json.loads(read_regular_text(manifest_path)) if manifest_path.exists() else {"files": {}}
    interpreter = previous.get("python_executable", sys.executable) if args.status else sys.executable
    interpreter = os.path.abspath(interpreter)
    expected = {}
    for name in release["skills"]:
        source = SOURCE / name
        for path in (source / "SKILL.md",):
            text = read_regular_text(path)
            if path.name == "SKILL.md":
                text = text.replace("<python>", shlex.quote(interpreter)).replace("<tools>", shlex.quote(str(tools))).replace("<config>", shlex.quote(str(config)))
                text += f"\n## Installed runtime\n\nPython interpreter: `{interpreter}`.\nTools directory: `{tools}`.\nConfig file: `{config}`.\nUse this interpreter for the installed tools; it retains the installation environment.\n"
            expected[checked(destination / name / path.relative_to(source))] = text
    for name in release["tools"]:
        expected[checked(tools / name)] = read_regular_text(SOURCE.parent / "scripts" / name)
    allowed = (destination, tools)
    old = {}
    for raw, sha in previous["files"].items():
        path = checked(raw)
        if not any(parent in path.parents for parent in allowed):
            raise KBConfigError("installed manifest contains a path outside managed directories")
        old[path] = sha
    legacy = json.loads(read_regular_text(SOURCE / "legacy-manifest.json"))
    legacy_conflicts = []
    for relative, sha in legacy["files"].items():
        path = checked(destination / relative)
        if path.exists() and path not in old:
            if digest(read_regular_text(path)) == sha:
                old[path] = sha
            else:
                legacy_conflicts.append(str(path))
    states = {}
    for path, text in expected.items():
        actual = digest(read_regular_text(path)) if path.exists() else None
        states[str(path)] = "missing" if actual is None else "current" if actual == digest(text) else "modified"
    stale = [path for path in old if path not in expected and path.exists()]
    manifest_current = manifest_path.is_file() and previous.get("version") == release["version"]
    report = {"version": release["version"], "manifest_current": manifest_current, "legacy_conflicts": legacy_conflicts, "files": states, "stale": [str(p) for p in stale],
              "config_exists": config.is_file(), "python_executable": interpreter, "python_exists": Path(interpreter).is_file(),
              "mode": "status" if args.status else "dry-run" if args.dry_run else "install"}
    if args.status or args.dry_run:
        print(json.dumps(report, indent=2))
        return int(args.status and (not manifest_current or legacy_conflicts or any(value != "current" for value in states.values()) or bool(stale) or not config.is_file() or not Path(interpreter).is_file()))
    if legacy_conflicts:
        raise KBConfigError("customized retired skills require review before replacement: " + ", ".join(legacy_conflicts))
    changed = [path for path, state in states.items() if state == "modified"]
    if changed and not args.force:
        raise KBConfigError("existing package files differ; inspect --status, then use --force to replace them")
    for path in stale:
        if digest(read_regular_text(path)) != old[path]:
            raise KBConfigError(f"modified stale file needs manual review: {path}")
    config_text = "# KB domain mappings. No generic cross-domain fallback.\n"
    for domain, value in (("PERSONAL", args.personal_root), ("WORK", args.work_root)):
        if value:
            root = checked(value)
            if not root.is_dir():
                raise KBConfigError(f"{domain.lower()} root is not a directory")
            config_text += f"KB_{domain}_ROOT: {json.dumps(str(root), ensure_ascii=False)}\n"
    if not args.personal_root and not args.work_root:
        config_text += "# KB_PERSONAL_ROOT: /absolute/personal/knowledgebase\n# KB_WORK_ROOT: /absolute/work/knowledgebase\n"
    if config.exists():
        read_regular_text(config)
    for path, text in expected.items():
        if states[str(path)] != "current":
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(path, text)
    for path in stale:
        path.unlink()
    if not config.exists() or args.force_config:
        config.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(config, config_text)
    manifest = {"version": release["version"], "python_executable": interpreter,
                "files": {str(path): digest(text) for path, text in expected.items()}}
    atomic_write_text(manifest_path, json.dumps(manifest, indent=2) + "\n")
    print(f"Installed KB {release['version']}: {len(release['skills'])} skills, {len(release['tools'])} tools. Config preserved or created; values not displayed.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KBConfigError, OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)

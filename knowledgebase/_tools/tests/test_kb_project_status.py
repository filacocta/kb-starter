from __future__ import annotations

import datetime
import importlib.util
import os
import shlex
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "_tools" / "kb-project-status.py"
UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
VALID_SHA = "a" * 40


def load_status_module():
    spec = importlib.util.spec_from_file_location("kb_project_status_test", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def init_repo(path: Path, *, commit_date: str = "2026-09-10T10:00:00+00:00") -> str:
    path.mkdir(parents=True)
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
    (path / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    env = os.environ.copy()
    env["GIT_AUTHOR_DATE"] = commit_date
    env["GIT_COMMITTER_DATE"] = commit_date
    subprocess.run(
        [
            "git", "-C", str(path),
            "-c", "user.name=KB fixture",
            "-c", "user.email=kb-fixture@example.invalid",
            "commit", "-qm", "fixture commit",
        ],
        check=True,
        env=env,
    )
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def make_project(
    root: Path,
    *,
    location: str | None = None,
    label: str = "Code location",
    extra_frontmatter: str = "",
    todos: str | None = None,
) -> Path:
    project = root / "projects" / "example"
    project.mkdir(parents=True)
    location_line = f"\n**{label}:** `{location}`\n" if location is not None else ""
    summary = (
        "---\n"
        "author: kb-bot\n"
        "created: 2026-09-01\n"
        "modified: 2026-09-10\n"
        "type: summary\n"
        "topic: example\n"
        "title: Example\n"
        "description: Example project.\n"
        "status: active\n"
        f"{extra_frontmatter}"
        "---\n\n"
        "# Example\n"
        f"{location_line}"
    )
    (project / "_summary.md").write_text(summary, encoding="utf-8")
    (project / "_todos.md").write_text(
        todos
        or "## Backlog\n\n## In Progress\n\n## Testing / Review\n\n## Done\n",
        encoding="utf-8",
    )
    return project


def record(repo_id: str, commit: str, *, dirty: bool = False, observed_at: str = "2026-09-10T10:00:00Z") -> str:
    return (
        f"code_state_repo_id: {repo_id}\n"
        f"code_state_commit: {commit}\n"
        f"code_state_dirty: {'true' if dirty else 'false'}\n"
        f"code_state_observed_at: {observed_at}\n"
        "code_state_scope: repository-git-metadata\n"
    )


def test_absent_record_is_unknown_and_not_a_warning():
    module = load_status_module()
    parsed = module.parse_code_state({}, now=NOW)
    assert parsed == {
        "status": "absent",
        "present": False,
        "record_valid": False,
        "fields": {},
        "warnings": [],
    }


def test_partial_invalid_and_future_records_never_verify():
    module = load_status_module()
    partial = module.parse_code_state({"code_state_repo_id": "/tmp/repo"}, now=NOW)
    assert partial["status"] == "partial"
    assert partial["record_valid"] is False
    assert partial["warnings"]

    invalid = module.parse_code_state(
        {
            "code_state_repo_id": "relative/.git",
            "code_state_commit": "short",
            "code_state_dirty": "maybe",
            "code_state_observed_at": "2026-09-10T10:00:00",
            "code_state_scope": "tests-passed",
        },
        now=NOW,
    )
    assert invalid["status"] == "invalid"
    assert invalid["record_valid"] is False
    assert len(invalid["warnings"]) >= 4

    future = module.parse_code_state(
        {
            "code_state_repo_id": "/tmp/repo/.git",
            "code_state_commit": VALID_SHA,
            "code_state_dirty": False,
            "code_state_observed_at": "2026-09-11T10:00:00Z",
            "code_state_scope": "repository-git-metadata",
        },
        now=NOW,
    )
    assert future["status"] == "future"
    assert future["record_valid"] is False
    assert any("future" in warning for warning in future["warnings"])


def test_status_collects_metadata_without_runtime_checks(tmp_path: Path, monkeypatch):
    module = load_status_module()
    repo = tmp_path / "repo"
    commit = init_repo(repo)
    project = make_project(tmp_path, location=str(repo))

    calls: list[Path] = []
    original_git_info = module.git_info
    monkeypatch.setattr(module, "git_info", lambda path: calls.append(path) or original_git_info(path))
    monkeypatch.setattr(module, "venv_info", lambda path: (_ for _ in ()).throw(AssertionError("venv executed")))
    monkeypatch.setattr(module, "systemd_units", lambda slug, path: (_ for _ in ()).throw(AssertionError("systemd executed")))
    result = module.status_for(project, now=NOW)

    assert result["code_location"] == str(repo)
    assert result["code_location_status"] == "ok"
    assert result["git"]["commit"] == commit
    assert len(result["git"]["commit"]) == 40
    assert result["git"]["repo_id"] == str((repo / ".git").resolve())
    assert result["git"]["dirty"] is False
    assert result["venvs"] is None
    assert result["systemd_units"] is None
    assert calls == [repo]
    current = result["code_state"]["current_observation"]
    assert current["status"] == "observed"
    assert current["observed_at"] == "2026-09-10T12:00:00+00:00"


def test_runtime_checks_are_explicit_and_separate(tmp_path: Path, monkeypatch):
    module = load_status_module()
    repo = tmp_path / "repo"
    init_repo(repo)
    project = make_project(tmp_path, location=str(repo))
    calls: list[str] = []
    monkeypatch.setattr(module, "venv_info", lambda path: calls.append("venv") or [{"ok": True}])
    monkeypatch.setattr(module, "systemd_units", lambda slug, path: calls.append("systemd") or [{"active": "inactive"}])

    result = module.status_for(project, runtime_checks=True, now=NOW)
    assert calls == ["venv", "systemd"]
    assert result["venvs"] == [{"ok": True}]
    assert result["systemd_units"] == [{"active": "inactive"}]


def test_code_state_match_does_not_claim_unchanged_working_tree(tmp_path: Path):
    module = load_status_module()
    repo = tmp_path / "repo"
    commit = init_repo(repo)
    project = make_project(
        tmp_path,
        location=str(repo),
        extra_frontmatter=record(str((repo / ".git").resolve()), commit, dirty=True),
    )
    # Make the live metadata dirty while preserving the recorded dirty flag.
    (repo / "local.txt").write_text("uncommitted\n", encoding="utf-8")
    result = module.status_for(project, now=NOW)
    comparison = result["code_state"]["comparison"]
    assert comparison["status"] == "metadata-match"
    assert comparison["metadata_match"] is True
    assert comparison["working_tree_content_unchanged"] is None
    assert "does not prove" in comparison["dirty_note"]


def test_code_state_divergence_and_age_are_separate_advisories(tmp_path: Path):
    module = load_status_module()
    repo = tmp_path / "repo"
    commit = init_repo(repo)
    project = make_project(
        tmp_path,
        location=str(repo),
        extra_frontmatter=record(
            str((repo / ".git").resolve()),
            "b" * 40,
            observed_at="2026-08-01T10:00:00Z",
        ),
    )
    result = module.status_for(project, now=NOW, stale_days=21)
    comparison = result["code_state"]["comparison"]
    assert comparison["status"] == "diverged"
    assert comparison["age_advisory"] == "stale"
    assert comparison["fields"]["commit"]["status"] == "mismatch"
    assert result["code_state"]["record"]["status"] == "valid"
    assert result["code_state"]["record"]["record_valid"] is True
    assert result["code_state"]["record"]["fields"]["commit"] != commit


def test_git_failure_is_explicit(tmp_path: Path, monkeypatch):
    module = load_status_module()
    repo = tmp_path / "not-a-repo"
    repo.mkdir()
    monkeypatch.setattr(module, "run", lambda *args, **kwargs: (1, "", "git fixture failure"))
    info = module.git_info(repo)
    assert info["is_repo"] is False
    assert "git fixture failure" in info["errors"][0]
    observation = module.current_code_observation(repo, now=NOW)
    assert observation["status"] == "unavailable"
    assert observation["observation_complete"] is False


def test_worktree_uses_git_common_dir_identity(tmp_path: Path):
    module = load_status_module()
    repo = tmp_path / "repo"
    commit = init_repo(repo)
    worktree = tmp_path / "worktree"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "--detach", "-q", str(worktree)],
        check=True,
    )
    assert (worktree / ".git").is_file()
    info = module.git_info(worktree)
    assert info["is_repo"] is True
    assert info["repo_root"] == str(worktree.resolve())
    assert info["repo_id"] == str((repo / ".git").resolve())
    assert info["commit"] == commit


def test_location_labels_and_explicit_failure_states(tmp_path: Path):
    module = load_status_module()
    repo = tmp_path / "repo"
    init_repo(repo)
    for label in module.LOCATION_LABELS:
        result = module.resolve_code_location(f"**{label}:** `{repo}`")
        assert result["status"] == "ok", label

    assert module.resolve_code_location("**Code location:** `n/a`")["status"] == "explicit-missing"
    assert module.resolve_code_location("**Code location:** not created yet")["status"] == "explicit-missing"
    assert module.resolve_code_location("**Code location:** `relative/repo`")["status"] == "relative"
    assert module.resolve_code_location("**Code location:** `/tmp/does-not-exist-kb-fixture`")["status"] == "not-found"
    file_path = tmp_path / "file"
    file_path.write_text("fixture\n", encoding="utf-8")
    assert module.resolve_code_location(f"**Code location:** `{file_path}`")["status"] == "non-directory"
    ambiguous = module.resolve_code_location(
        f"**Code location:** `{repo}`\n**Live app location:** `{repo}`"
    )
    assert ambiguous["status"] == "ambiguous"
    assert len(ambiguous["locations"]) == 2


def test_non_root_folder_does_not_report_parent_repository(tmp_path: Path):
    module = load_status_module()
    repo = tmp_path / "repo"
    init_repo(repo)
    child = repo / "src"
    child.mkdir()
    info = module.git_info(child)
    assert info["is_repo"] is False
    assert "not the Git repository root" in info["errors"][0]


def test_status_supports_code_paper_location_and_legacy_date_heuristic(tmp_path: Path):
    module = load_status_module()
    repo = tmp_path / "repo"
    init_repo(repo, commit_date="2026-08-01T10:00:00+00:00")
    project = make_project(tmp_path, location=str(repo), label="Code / paper location")
    # Keep a known summary date so the legacy field is emitted.
    summary = project / "_summary.md"
    summary.write_text(summary.read_text().replace("modified: 2026-09-10", "modified: 2026-07-01"), encoding="utf-8")
    result = module.status_for(project, now=NOW)
    assert result["code_location_status"] == "ok"
    assert result["summary_date_comparison"]["kind"] == "document-date heuristic"
    assert result["summary_date_comparison"]["implementation_verified"] is False


def test_cli_rejects_path_traversal_and_all_slug_conflict(tmp_path: Path, capsys):
    module = load_status_module()
    (tmp_path / "projects").mkdir()
    assert module.main(["--kb-root", str(tmp_path), "../outside"]) == 2
    assert "invalid project slug" in capsys.readouterr().out
    assert module.main(["--kb-root", str(tmp_path), "--all", "example"]) == 2
    assert "either --all" in capsys.readouterr().out


def test_status_without_location_is_explicit_unknown(tmp_path: Path):
    module = load_status_module()
    project = make_project(tmp_path)
    result = module.status_for(project, now=NOW)
    assert result["code_location_status"] == "missing-label"
    assert result["code_state"]["comparison"]["status"] == "unknown"
    assert result["code_state"]["record"]["status"] == "absent"


def test_frontmatter_rejects_quoted_boolean_duplicates_and_nested_values():
    module = load_status_module()
    text = (
        "---\n"
        "code_state_repo_id: /tmp/repo/.git\n"
        f"code_state_commit: {VALID_SHA}\n"
        'code_state_dirty: "false"\n'
        "code_state_dirty: false\n"
        "  note: nested\n"
        "code_state_observed_at: 2026-09-10T10:00:00Z\n"
        "code_state_scope: repository-git-metadata\n"
        "---\n"
    )
    fm, errors = module.parse_frontmatter(text)
    parsed = module.parse_code_state(fm, frontmatter_errors=errors, now=NOW)
    assert parsed["status"] == "invalid"
    assert parsed["record_valid"] is False
    assert any("actual YAML boolean" in warning for warning in parsed["warnings"])
    assert any("duplicate frontmatter key" in warning for warning in parsed["warnings"])
    assert any("invalid code_state_ YAML" in warning for warning in parsed["warnings"])


def test_frontmatter_rejects_quoted_and_plain_duplicate_code_state_key():
    module = load_status_module()
    text = (
        "---\n"
        "code_state_repo_id: /tmp/repo/.git\n"
        f"code_state_commit: {VALID_SHA}\n"
        "code_state_dirty: false\n"
        '"code_state_dirty": false\n'
        "code_state_observed_at: 2026-09-10T10:00:00Z\n"
        "code_state_scope: repository-git-metadata\n"
        "---\n"
    )
    fm, errors = module.parse_frontmatter(text)
    parsed = module.parse_code_state(fm, frontmatter_errors=errors, now=NOW)
    assert parsed["record_valid"] is False
    assert any("duplicate key" in warning for warning in parsed["warnings"])


def test_git_environment_ignores_inherited_repository_selectors(monkeypatch):
    module = load_status_module()
    selectors = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE")
    for selector in selectors:
        monkeypatch.setenv(selector, "/tmp/wrong-repository-selector")
    monkeypatch.setenv("GIT_NO_LAZY_FETCH", "0")
    env = module._git_env()
    assert all(selector not in env for selector in selectors)
    assert env["GIT_OPTIONAL_LOCKS"] == "0"
    assert env["GIT_NO_LAZY_FETCH"] == "1"


def test_git_info_rejects_mixed_head_snapshot_and_comparison(tmp_path: Path, monkeypatch):
    module = load_status_module()
    repo = tmp_path / "repo"
    init_repo(repo)
    initial_head = "a" * 40
    final_head = "b" * 40
    head_reads = 0
    original = module._git_run

    def mixed_git(path, args, timeout=5):
        nonlocal head_reads
        if args == ["rev-parse", "HEAD"]:
            head_reads += 1
            return 0, (initial_head if head_reads == 1 else final_head), ""
        if args and args[0] == "status":
            return 0, "", ""
        if args and args[0] == "log":
            return 0, "2026-09-10\x00fixture commit", ""
        return original(path, args, timeout)

    monkeypatch.setattr(module, "_git_run", mixed_git)
    info = module.git_info(repo)

    assert head_reads == 2
    assert info["head_initial"] == initial_head
    assert info["head_final"] == final_head
    assert info["commit"] is None
    assert any("HEAD changed during metadata observation" in error for error in info["errors"])

    observation = module.current_code_observation(repo, git=info, now=NOW)
    assert observation["observation_complete"] is False
    comparison = module.compare_code_state(
        {
            "status": "valid",
            "record_valid": True,
            "fields": {
                "repo_id": info["repo_id"],
                "commit": initial_head,
                "dirty": False,
                "scope": module.CODE_STATE_SCOPE,
            },
        },
        observation,
        now=NOW,
    )
    assert comparison["status"] == "unverified"
    assert comparison["metadata_match"] is None


def test_git_info_reports_final_head_read_failure_without_retry(tmp_path: Path, monkeypatch):
    module = load_status_module()
    repo = tmp_path / "repo"
    init_repo(repo)
    head = "a" * 40
    head_reads = 0
    original = module._git_run

    def final_failure_git(path, args, timeout=5):
        nonlocal head_reads
        if args == ["rev-parse", "HEAD"]:
            head_reads += 1
            if head_reads == 1:
                return 0, head, ""
            return 1, "", "final HEAD fixture failure"
        if args and args[0] == "status":
            return 0, "", ""
        if args and args[0] == "log":
            return 0, "2026-09-10\x00fixture commit", ""
        return original(path, args, timeout)

    monkeypatch.setattr(module, "_git_run", final_failure_git)
    info = module.git_info(repo)

    assert head_reads == 2
    assert info["head_initial"] == head
    assert info["head_final"] is None
    assert info["commit"] is None
    assert any("final HEAD read failed" in error for error in info["errors"])
    assert any("final HEAD fixture failure" in error for error in info["errors"])
    observation = module.current_code_observation(repo, git=info, now=NOW)
    assert observation["observation_complete"] is False


def test_status_does_not_execute_configured_clean_filter(tmp_path: Path):
    module = load_status_module()
    repo = tmp_path / "repo"
    init_repo(repo)
    setup_marker = tmp_path / "clean-filter-setup-invoked"
    status_marker = tmp_path / "clean-filter-status-invoked"
    clean_command = f"printf invoked > {shlex.quote(str(setup_marker))}; cat"
    subprocess.run(
        ["git", "-C", str(repo), "config", "filter.evil.clean", clean_command],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "filter.evil.required", "true"],
        check=True,
    )
    (repo / ".gitattributes").write_text("README.md filter=evil\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", ".gitattributes"], check=True)
    subprocess.run(
        [
            "git", "-C", str(repo),
            "-c", "user.name=KB fixture",
            "-c", "user.email=kb-fixture@example.invalid",
            "commit", "-qm", "filter attributes",
        ],
        check=True,
    )
    # The commit may refresh the index and invoke the configured filter. Point
    # the same driver at a fresh marker immediately before the observation so
    # the assertion covers only the status command under test.
    clean_command = f"printf invoked > {shlex.quote(str(status_marker))}; cat"
    subprocess.run(
        ["git", "-C", str(repo), "config", "filter.evil.clean", clean_command],
        check=True,
    )

    info = module.git_info(repo)
    assert info["is_repo"] is True
    assert info["dirty"] is None
    assert any("configured Git filters were disabled" in error for error in info["errors"])
    assert not status_marker.exists()


def test_current_observation_can_use_one_supplied_git_read(monkeypatch, tmp_path: Path):
    module = load_status_module()
    monkeypatch.setattr(module, "git_info", lambda path: (_ for _ in ()).throw(AssertionError("git reread")))
    observation = module.current_code_observation(
        tmp_path,
        now=NOW,
        git={
            "is_repo": True,
            "repo_id": "/tmp/repo/.git",
            "commit": VALID_SHA,
            "dirty": False,
            "dirty_files": 0,
            "errors": [],
        },
    )
    assert observation["status"] == "observed"
    assert observation["observation_complete"] is True
    assert "atomic snapshot" in observation["observation_note"]


def test_dirty_status_failure_is_partial_and_not_unknown_clean(tmp_path: Path, monkeypatch):
    module = load_status_module()
    repo = tmp_path / "repo"
    init_repo(repo)
    original = module._git_run

    def fail_status(path, args, timeout=5):
        if args and args[0] == "status":
            return 1, "", "status fixture failure"
        return original(path, args, timeout)

    monkeypatch.setattr(module, "_git_run", fail_status)
    info = module.git_info(repo)
    assert info["is_repo"] is True
    assert info["dirty"] is None
    assert any("status fixture failure" in error for error in info["errors"])
    observation = module.current_code_observation(repo, git=info, now=NOW)
    assert observation["status"] == "partial"
    assert observation["observation_complete"] is False


def test_location_parser_ignores_comments_fences_and_blockquotes(tmp_path: Path):
    module = load_status_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    text = (
        "<!-- **Code location:** `/tmp/commented` -->\n"
        "```markdown\n"
        "<!-- literal comment in fenced example -->\n"
        "**Code location:** `/tmp/fenced`\n"
        "```\n"
        "> **Code location:** `/tmp/quoted`\n"
        f"**Code location:** `{repo}`\n"
    )
    result = module.resolve_code_location(text)
    assert result["status"] == "ok"
    assert result["path"] == str(repo)
    assert module.resolve_code_location(text.split(f"**Code location:** `{repo}`")[0])["status"] == "missing-label"

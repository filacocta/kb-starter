#!/usr/bin/env python3
"""Read-only status snapshots for KB projects.

The default path collects repository metadata only. It never runs a project
interpreter, starts a service, or writes a code-state record. Runtime checks
(``venv_info`` and ``systemd_units``) are available only through the explicit
``--runtime-checks`` option.

The optional flat ``code_state_*`` fields in a project summary are evidence of
one Git metadata observation, not a claim that the implementation works, has
been tested, or is deployed. Existing summaries may omit the complete record;
absence is reported as unknown for backwards compatibility.

Usage:
  kb-project-status.py --all
  kb-project-status.py [--runtime-checks] <slug>
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable

# PyYAML is already a dependency of the shared KB write/queue tools. It is
# used here only when a summary opts into the code-state record.
import yaml


LOCATION_LABELS = (
    "Code location",
    "Live app location",
    "Vault project location",
    "Code / paper location",
)
LOCATION_RE = re.compile(
    r"\*\*(?P<label>Code location|Live app location|Vault project location|Code / paper location):\*\*\s*(?P<value>`[^`\n]+`|(?:(?!\*\*)[^\n])+)"
)
MISSING_LOCATION_VALUES = {
    "n/a",
    "na",
    "none",
    "not created",
    "not created yet",
    "not applicable",
    "unassigned",
}
REQUIRED_FILES = ("_summary.md", "_todos.md")
DEFAULT_STALE_DAYS = 21
CODE_STATE_SCOPE = "repository-git-metadata"
CODE_STATE_FIELDS = (
    "code_state_repo_id",
    "code_state_commit",
    "code_state_dirty",
    "code_state_observed_at",
    "code_state_scope",
)
CODE_STATE_FIELD_NAMES = tuple(field.removeprefix("code_state_") for field in CODE_STATE_FIELDS)
SHA_RE = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FILTER_CONFIG_NAME_RE = re.compile(
    r"^filter\.(?P<driver>.+)\.(?P<option>clean|process|smudge|required)$",
    re.IGNORECASE,
)
FILTER_CONFIG_PATTERN = r"^filter\..+\.(clean|process|smudge|required)$"
FILTER_NEUTRALIZED_WARNING = (
    "working-tree dirtiness is unknown because configured Git filters were disabled for the safe status read"
)
ISO_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})$"
)
FENCE_RE = re.compile(r"^(?P<indent> {0,3})(?P<run>`{3,}|~{3,})(?P<tail>.*)$")


def parse_iso(value: str | None) -> datetime.date | None:
    """Parse the date prefix used by legacy summary comparisons."""
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate keys, including quoted aliases."""

    def construct_mapping(self, node, deep=False):  # type: ignore[override]
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in mapping:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def _frontmatter_block(text: str) -> str | None:
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end == -1:
        return None
    return text[4:end]


CODE_STATE_MARKER_RE = re.compile(
    r"^\s*(?:-\s*)?(?:['\"])?code_state(?:_|:|['\"]|\s|$)", re.MULTILINE
)


def _has_nested_code_state(value: Any, *, top_level: bool = True) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str) and key.startswith("code_state_") and not top_level:
                return True
            if _has_nested_code_state(child, top_level=False):
                return True
    elif isinstance(value, list):
        return any(_has_nested_code_state(item, top_level=False) for item in value)
    return False


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], list[str]]:
    block = _frontmatter_block(text)
    if not text.startswith("---\n"):
        return {}, ["summary has no YAML frontmatter"]
    if block is None:
        errors = ["summary has unterminated YAML frontmatter"]
        if CODE_STATE_MARKER_RE.search(text):
            errors.append("code_state_ fields occur in unterminated frontmatter")
        return {}, errors
    fm: dict[str, Any] = {}
    errors: list[str] = []
    for line in block.splitlines():
        if ":" not in line or line.startswith((" ", "-")):
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if key in fm:
            errors.append(f"duplicate frontmatter key {key!r}")
            continue
        raw_value = value.strip()
        fm[key] = raw_value if key.startswith("code_state_") else raw_value.strip('"').strip("'")

    # Legacy summaries retain the lightweight flat projection above. Once a
    # code-state key is declared, however, actual YAML types and unique keys
    # are required; this also catches quoted/unquoted duplicate key spellings.
    if CODE_STATE_MARKER_RE.search(block):
        try:
            strict = yaml.load(block, Loader=_UniqueKeyLoader)
        except (yaml.YAMLError, TypeError, ValueError, RecursionError) as exc:
            errors.append(f"invalid code_state_ YAML: {exc}")
        else:
            if not isinstance(strict, dict):
                errors.append("code_state_ frontmatter must be a YAML mapping")
            else:
                if _has_nested_code_state(strict):
                    errors.append("code_state_ fields must be top-level flat YAML keys")
                for key, value in strict.items():
                    if isinstance(key, str) and key.startswith("code_state"):
                        if key == "code_state" or not key.startswith("code_state_"):
                            errors.append(f"unsupported code_state_ key {key!r}")
                        else:
                            fm[key] = value
    return fm, errors


def frontmatter(text: str) -> dict[str, Any]:
    """Return the legacy flat frontmatter projection.

    Project code-state fields intentionally remain flat scalar fields. This
    keeps old lightweight parsers compatible and avoids silently ignoring a
    nested map in a summary that has not opted into a stricter YAML parser.
    """
    return _parse_frontmatter(text)[0]


def parse_frontmatter(text: str) -> tuple[dict[str, Any], list[str]]:
    """Return legacy metadata plus strict YAML values for declared code state."""
    return _parse_frontmatter(text)


def _now_utc(now: datetime.datetime | None = None) -> datetime.datetime:
    value = now or datetime.datetime.now(datetime.timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=datetime.timezone.utc)
    return value.astimezone(datetime.timezone.utc)


def _iso_timestamp(now: datetime.datetime | None = None) -> str:
    return _now_utc(now).isoformat(timespec="seconds")


def _parse_timestamp(value: Any) -> datetime.datetime | None:
    if isinstance(value, datetime.datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return None
        return value.astimezone(datetime.timezone.utc)
    if isinstance(value, datetime.date) or not isinstance(value, str):
        return None
    if not value or not ISO_TIMESTAMP_RE.fullmatch(value.strip()):
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(datetime.timezone.utc)


def _json_scalar(value: Any) -> Any:
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return repr(value)


def _markdown_body(text: str) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[end + 5 :]
    return text


def visible_markdown_lines(text: str) -> Iterable[tuple[int, str]]:
    """Yield ``(line_number, line)`` values outside Markdown examples."""
    fence_char: str | None = None
    fence_length = 0
    in_comment = False
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        # Fence state is determined from the raw line first. A literal HTML
        # comment inside an example must not hide a real fence closer.
        fence = FENCE_RE.match(raw_line)
        if fence:
            run = fence.group("run")
            if fence_char is None:
                fence_char, fence_length = run[0], len(run)
            elif run[0] == fence_char and len(run) >= fence_length and not fence.group("tail").strip():
                fence_char, fence_length = None, 0
            continue
        if fence_char is not None:
            continue

        remaining = raw_line
        visible_parts: list[str] = []
        while remaining:
            if in_comment:
                end = remaining.find("-->")
                if end == -1:
                    remaining = ""
                    break
                in_comment = False
                remaining = remaining[end + 3 :]
                continue
            start = remaining.find("<!--")
            if start == -1:
                visible_parts.append(remaining)
                remaining = ""
                break
            visible_parts.append(remaining[:start])
            in_comment = True
            remaining = remaining[start + 4 :]
        line = "".join(visible_parts)
        if re.match(r"^ {4}", line) or re.match(r"^ {0,3}>", line):
            continue
        yield line_number, line


def parse_code_state(
    fm: dict[str, Any],
    *,
    frontmatter_errors: Iterable[str] = (),
    now: datetime.datetime | None = None,
) -> dict[str, Any]:
    """Parse the optional all-or-nothing project code-state record.

    The result is JSON-serialisable. ``record_valid`` means only that the
    strict YAML record is structurally valid and not future-dated; it says
    nothing about code correctness or implementation readiness.
    """
    present_keys = [key for key in fm if isinstance(key, str) and key.startswith("code_state_")]
    code_state_errors = [error for error in frontmatter_errors if "code_state_" in error]
    if not present_keys and not code_state_errors:
        return {
            "status": "absent",
            "present": False,
            "record_valid": False,
            "fields": {},
            "warnings": [],
        }

    warnings: list[str] = list(code_state_errors)
    fields = {name: fm.get(f"code_state_{name}") for name in CODE_STATE_FIELD_NAMES}
    unknown = sorted(set(present_keys) - set(CODE_STATE_FIELDS))
    if unknown:
        warnings.append("unknown code-state field(s): " + ", ".join(unknown))

    repo_id = fields["repo_id"]
    commit = fields["commit"]
    dirty_raw = fields["dirty"]
    observed_raw = fields["observed_at"]
    scope = fields["scope"]
    missing = [
        name for name, value in fields.items()
        if value is None or (isinstance(value, str) and not value.strip())
    ]
    if missing:
        warnings.append("code-state record is partial; missing: " + ", ".join(missing))
    if repo_id is not None and not isinstance(repo_id, str):
        warnings.append("code_state_repo_id must be a scalar string value")
    if isinstance(repo_id, str) and repo_id.strip():
        repo_path = Path(repo_id)
        if not repo_path.is_absolute():
            warnings.append("code_state_repo_id must be an absolute resolved Git common directory")
        else:
            try:
                if str(repo_path.resolve()) != str(repo_path):
                    warnings.append("code_state_repo_id must be an absolute resolved Git common directory")
            except (OSError, RuntimeError):
                warnings.append("code_state_repo_id could not be resolved")
    if commit is not None and not isinstance(commit, str):
        warnings.append("code_state_commit must be a full hexadecimal SHA string")
    elif isinstance(commit, str) and commit.strip() and not SHA_RE.fullmatch(commit):
        warnings.append("code_state_commit must be a full 40- or 64-character hexadecimal SHA")
    dirty: bool | None = None
    if isinstance(dirty_raw, bool):
        dirty = dirty_raw
    elif dirty_raw is not None:
        warnings.append("code_state_dirty must be an actual YAML boolean (true or false), not a quoted/string value")
    observed = _parse_timestamp(observed_raw)
    if observed_raw is not None and observed is None:
        warnings.append("code_state_observed_at must be an RFC3339 timestamp with timezone")
    if scope is not None and not isinstance(scope, str):
        warnings.append("code_state_scope must be a scalar string value")
    elif isinstance(scope, str) and scope.strip() and scope != CODE_STATE_SCOPE:
        warnings.append(f"code_state_scope must be exactly {CODE_STATE_SCOPE!r}")

    current_time = _now_utc(now)
    future = bool(observed and observed > current_time)
    if future:
        warnings.append("code_state_observed_at is in the future")

    valid = not missing and not warnings
    normalized_fields: dict[str, Any] = {
        "repo_id": _json_scalar(repo_id),
        "commit": commit.lower() if isinstance(commit, str) else _json_scalar(commit),
        "dirty": dirty if dirty is not None else _json_scalar(dirty_raw),
        "observed_at": _json_scalar(observed_raw),
        "scope": _json_scalar(scope),
    }
    if future:
        status = "future"
    elif warnings and not (len(warnings) == 1 and missing):
        status = "invalid"
    elif missing:
        status = "partial"
    else:
        status = "valid"
    return {
        "status": status,
        "present": True,
        "record_valid": valid and not future,
        "fields": normalized_fields,
        "warnings": warnings,
    }


def run(
    cmd: list[str],
    timeout: int = 5,
    *,
    env: dict[str, str] | None = None,
) -> tuple[int | None, str, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False, env=env)
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "", str(exc)


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    # Metadata reads should not take optional index locks, start fsmonitor, or
    # wait for an interactive credential/prompt path, or lazily fetch a
    # missing object from a partial clone. No fetch is performed.
    for selector in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE"):
        # An inherited selector can make `git -C <repo>` inspect a different
        # checkout. The named location is the authority for this observation.
        env.pop(selector, None)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_NO_LAZY_FETCH"] = "1"
    return env


def _git_filter_overrides(repo: Path) -> tuple[list[str], str | None, bool]:
    """Return command-line overrides that disable configured filter drivers.

    ``git status`` can invoke a repository-configured clean/process filter
    while comparing the worktree with the index. Only key names are requested
    from Git config; values (which may be executable commands) are never
    emitted or parsed. A config-discovery failure is fail-closed so the status
    command is not run without the overrides.
    """
    rc, names_text, stderr = _git_run(
        repo,
        ["config", "--name-only", "--get-regexp", FILTER_CONFIG_PATTERN],
    )
    if stderr:
        # A warning can indicate that an include or another config source was
        # not read completely. Do not run status when the effective filter
        # set is uncertain.
        return [], "could not enumerate Git filter configuration", False
    if rc == 1 and not names_text and not stderr:
        # `git config --get-regexp` uses status 1 when there are no matches.
        return [], None, False
    if rc != 0:
        return [], "could not enumerate Git filter configuration", False

    drivers: set[str] = set()
    for raw_name in names_text.splitlines():
        name = raw_name
        match = FILTER_CONFIG_NAME_RE.fullmatch(name)
        if not match or "\x00" in name or "\n" in name or "\r" in name:
            return [], "could not safely enumerate Git filter configuration", False
        drivers.add(match.group("driver"))

    overrides: list[str] = []
    for driver in sorted(drivers):
        # Empty filter commands disable clean/process/smudge execution. The
        # required override prevents a configured required=true from turning
        # those deliberately disabled filters into a status failure.
        for option in ("clean", "process", "smudge"):
            overrides.extend(["-c", f"filter.{driver}.{option}="])
        overrides.extend(["-c", f"filter.{driver}.required=false"])
    return overrides, None, bool(drivers)


def _git_run(repo: Path, args: list[str], timeout: int = 5) -> tuple[int | None, str, str]:
    command_args = list(args)
    filters_neutralized = False
    if command_args and command_args[0] == "status":
        overrides, error, filters_neutralized = _git_filter_overrides(repo)
        if error:
            return None, "", error
        command_args = [*overrides, *command_args]
    result = run(
        ["git", "-c", "core.fsmonitor=false", "-C", str(repo), *command_args],
        timeout=timeout,
        env=_git_env(),
    )
    if filters_neutralized:
        rc, stdout, stderr = result
        warning = FILTER_NEUTRALIZED_WARNING
        if stderr:
            warning = f"{warning}; Git status also reported: {stderr}"
        # Do not treat raw worktree-vs-index output as an ordinary dirtiness
        # result: disabling filters is necessary for safety but can change
        # Git's normalized-content semantics (for example Git LFS).
        return (None if rc == 0 else rc), stdout, warning
    return result


def _error_text(rc: int | None, stderr: str, fallback: str) -> str:
    if stderr:
        return stderr
    if rc is None:
        return fallback
    return f"command exited {rc}"


def _repo_root_and_common_dir(repo: Path) -> tuple[Path | None, str | None, list[str]]:
    errors: list[str] = []
    rc_root, root_text, root_err = _git_run(repo, ["rev-parse", "--show-toplevel"])
    if rc_root != 0 or not root_text:
        return None, None, [_error_text(rc_root, root_err, "git executable unavailable")]
    root = Path(root_text).expanduser()
    if not root.is_absolute():
        root = repo / root
    try:
        root = root.resolve()
        requested = repo.resolve()
    except (OSError, RuntimeError) as exc:
        return None, None, [f"could not resolve Git root: {exc}"]
    if requested != root:
        return root, None, [
            f"code location is not the Git repository root ({root}); refusing parent-repository false positive"
        ]

    rc_common, common_text, common_err = _git_run(repo, ["rev-parse", "--git-common-dir"])
    if rc_common != 0 or not common_text:
        errors.append(_error_text(rc_common, common_err, "could not resolve Git common directory"))
        return root, None, errors
    common = Path(common_text).expanduser()
    if not common.is_absolute():
        common = root / common
    try:
        return root, str(common.resolve()), errors
    except (OSError, RuntimeError) as exc:
        return root, None, [f"could not resolve Git common directory: {exc}"]


def git_info(repo: Path) -> dict[str, Any]:
    """Collect local Git metadata without fetching or running runtime hooks."""
    root, repo_id, root_errors = _repo_root_and_common_dir(repo)
    if root_errors or root is None or repo_id is None:
        return {
            "is_repo": False,
            "repo_root": str(root) if root else None,
            "repo_id": repo_id,
            "errors": root_errors or ["not a Git repository"],
        }

    errors: list[str] = []
    rc_branch, branch, branch_err = _git_run(repo, ["branch", "--show-current"])
    if rc_branch != 0:
        errors.append(_error_text(rc_branch, branch_err, "could not read current branch"))
        branch = ""

    rc_head, initial_head, head_err = _git_run(repo, ["rev-parse", "HEAD"])
    if rc_head != 0 or not SHA_RE.fullmatch(initial_head):
        errors.append(
            "initial HEAD read failed: "
            + _error_text(rc_head, head_err, "could not read HEAD commit")
        )
        initial_head = ""

    rc_dirty, dirty_text, dirty_err = _git_run(
        repo, ["status", "--porcelain=v1", "--untracked-files=all"]
    )
    dirty_lines = [line for line in dirty_text.splitlines() if line]
    if rc_dirty != 0:
        errors.append(_error_text(rc_dirty, dirty_err, "could not read working-tree status"))
        dirty: bool | None = None
        dirty_files: int | None = None
    else:
        dirty = bool(dirty_lines)
        dirty_files = len(dirty_lines)

    rc_last, last_text, last_err = _git_run(repo, ["log", "-1", "--format=%cs%x00%s"])
    if rc_last != 0 or not last_text:
        errors.append(_error_text(rc_last, last_err, "could not read last commit"))
        last_commit = None
        last_date = None
    else:
        date_text, _, subject = last_text.partition("\x00")
        last_date = parse_iso(date_text)
        last_commit = f"{date_text} {subject}".strip()
        if last_date is None:
            errors.append("last commit date was not ISO-formatted")

    # Read HEAD once more after every other metadata command. This bounded
    # guard detects a branch advance/reset during observation; there is no
    # automatic retry because retrying would only create another mixed window.
    rc_final_head, final_head, final_head_err = _git_run(repo, ["rev-parse", "HEAD"])
    if rc_final_head != 0 or not SHA_RE.fullmatch(final_head):
        errors.append(
            "final HEAD read failed: "
            + _error_text(rc_final_head, final_head_err, "could not read final HEAD commit")
        )
        final_head = ""
    elif initial_head and initial_head != final_head:
        errors.append(
            "HEAD changed during metadata observation "
            f"(initial {initial_head}, final {final_head})"
        )

    # ``commit`` is only a trusted snapshot value when both bounded reads
    # agree. Keep the individual reads for diagnosis, but do not expose a
    # potentially mixed commit as the current observation.
    commit = initial_head if initial_head and initial_head == final_head else ""

    info: dict[str, Any] = {
        "is_repo": True,
        "repo_root": str(root),
        "repo_id": repo_id,
        "branch": branch or None,
        "commit": commit or None,
        "head_initial": initial_head or None,
        "head_final": final_head or None,
        "dirty": dirty,
        "dirty_files": dirty_files,
        "last_commit": last_commit,
        "last_commit_date": last_date.isoformat() if last_date else None,
    }
    if errors:
        info["errors"] = errors
    return info


def resolve_code_location(text: str) -> dict[str, Any]:
    """Resolve one supported summary location and explain every failure mode."""
    matches = []
    for _, line in visible_markdown_lines(_markdown_body(text)):
        for match in LOCATION_RE.finditer(line):
            # A marker inside inline code is documentation/example text, not
            # the live project context line.
            if line[: match.start()].count("`") % 2:
                continue
            matches.append(
                {
                    "label": match.group("label"),
                    "raw": match.group("value").strip().strip("`").strip(),
                }
            )
    if not matches:
        return {
            "status": "missing-label",
            "path": None,
            "raw": None,
            "locations": [],
            "error": "no supported code-location label found",
        }
    if len(matches) > 1:
        return {
            "status": "ambiguous",
            "path": None,
            "raw": None,
            "locations": matches,
            "error": "multiple supported code-location labels found",
        }

    item = matches[0]
    raw = item["raw"]
    missing_value = raw.casefold().split("—", 1)[0].strip()
    if missing_value in MISSING_LOCATION_VALUES:
        return {
            "status": "explicit-missing",
            "path": None,
            "raw": raw,
            "locations": matches,
            "error": "summary explicitly says no code repository exists",
        }
    path = Path(raw).expanduser()
    if not path.is_absolute():
        return {
            "status": "relative",
            "path": str(path),
            "raw": raw,
            "locations": matches,
            "error": "code location must be an absolute path or ~/ path",
        }
    if not path.exists():
        return {
            "status": "not-found",
            "path": str(path),
            "raw": raw,
            "locations": matches,
            "error": "code location does not exist",
        }
    if not path.is_dir():
        return {
            "status": "non-directory",
            "path": str(path),
            "raw": raw,
            "locations": matches,
            "error": "code location exists but is not a directory",
        }
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError) as exc:
        return {
            "status": "error",
            "path": str(path),
            "raw": raw,
            "locations": matches,
            "error": f"could not resolve code location: {exc}",
        }
    item["resolved"] = str(resolved)
    return {
        "status": "ok",
        # Keep the historical expanded location in ``path`` for callers. The
        # canonical path is available separately for Git identity/observation.
        "path": str(path),
        "resolved": str(resolved),
        "raw": raw,
        "locations": matches,
        "error": None,
    }


def _observation_from_git(
    git: dict[str, Any],
    *,
    now: datetime.datetime | None = None,
) -> dict[str, Any]:
    """Turn one Git metadata read into a timestamped, non-persistent observation."""
    observed_at = _iso_timestamp(now)
    if not git.get("is_repo"):
        return {
            "status": "unavailable",
            "scope": CODE_STATE_SCOPE,
            "repo_id": git.get("repo_id"),
            "commit": None,
            "dirty": None,
            "dirty_files": None,
            "observed_at": observed_at,
            "observation_complete": False,
            "observation_note": "Git metadata was unavailable; no implementation or runtime claim is made",
            "errors": git.get("errors", ["not a Git repository"]),
        }
    errors = list(git.get("errors", []))
    complete = bool(git.get("repo_id") and git.get("commit") and git.get("dirty") is not None and not errors)
    filter_warning = any(FILTER_NEUTRALIZED_WARNING in error for error in errors)
    return {
        "status": "observed" if complete else "partial",
        "scope": CODE_STATE_SCOPE,
        "repo_id": git.get("repo_id"),
        "commit": git.get("commit"),
        "dirty": git.get("dirty"),
        "dirty_files": git.get("dirty_files"),
        "observed_at": observed_at,
        "observation_complete": complete,
        "observation_note": (
            (
                "Git status used a filter-neutralized read, so dirtiness is unknown; "
                "Git HEAD/status metadata were read as separate local commands; "
                "this is not an atomic snapshot of content"
            )
            if filter_warning
            else "Git HEAD/status metadata were read as separate local commands; this is not an atomic snapshot of content"
        ),
        "errors": errors,
    }


def current_code_observation(
    code_path: Path,
    *,
    now: datetime.datetime | None = None,
    git: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a timestamped Git metadata observation; never writes it."""
    return _observation_from_git(git if git is not None else git_info(code_path), now=now)


def compare_code_state(
    record: dict[str, Any],
    current: dict[str, Any] | None,
    *,
    now: datetime.datetime | None = None,
    stale_days: int = DEFAULT_STALE_DAYS,
) -> dict[str, Any]:
    """Compare an optional persisted record with the current local observation."""
    result: dict[str, Any] = {
        "status": "unknown",
        "metadata_match": None,
        "fields": {},
        "age_days": None,
        "age_advisory": None,
        "working_tree_content_unchanged": None,
        "warnings": [],
    }
    record_status = record.get("status")
    if record_status == "absent":
        result["reason"] = "no persisted code-state record"
        return result
    if not record.get("record_valid"):
        result["status"] = "unverified"
        result["reason"] = f"persisted record is {record_status}"
        result["warnings"] = list(record.get("warnings", []))
        return result

    observed = _parse_timestamp(record.get("fields", {}).get("observed_at"))
    current_time = _now_utc(now)
    if observed:
        age_seconds = (current_time - observed).total_seconds()
        if age_seconds >= 0:
            result["age_days"] = int(age_seconds // 86400)
            if result["age_days"] > stale_days:
                result["age_advisory"] = "stale"
        else:
            result["age_advisory"] = "future"
    if not current or current.get("status") not in {"observed", "partial"}:
        result["status"] = "unverified"
        result["reason"] = "current repository observation unavailable"
        return result

    expected = record["fields"]
    actual = current
    comparisons: dict[str, dict[str, Any]] = {}
    observation_complete = not current.get("errors") and current.get("observation_complete") is True
    for field in ("repo_id", "commit", "dirty", "scope"):
        expected_value = expected.get(field)
        actual_value = actual.get(field)
        matches = expected_value == actual_value
        comparisons[field] = {
            "status": "match" if matches else "mismatch",
            "expected": expected_value,
            "actual": actual_value,
        }
    result["fields"] = comparisons
    # A partial or error-bearing observation cannot verify a persisted record,
    # even if the fields read before the failure happen to equal it.
    result["metadata_match"] = (
        all(item["status"] == "match" for item in comparisons.values())
        if observation_complete
        else None
    )
    result["working_tree_content_unchanged"] = None
    result["dirty_note"] = (
        "dirty-state equality is only a metadata match; it does not prove that working-tree content is unchanged"
    )
    if not observation_complete:
        result["status"] = "unverified"
        result["reason"] = "current repository observation is partial"
    elif result["metadata_match"]:
        result["status"] = "metadata-match"
    else:
        result["status"] = "diverged"
        result["reason"] = "persisted code-state metadata differs from current observation"
    return result


def venv_info(code_path: Path) -> list[dict[str, Any]]:
    """Inspect discovered Python environments (explicit runtime opt-in only)."""
    venvs: list[dict[str, Any]] = []
    for cfg in code_path.rglob("pyvenv.cfg"):
        parts = set(cfg.relative_to(code_path).parts)
        if parts & {".git", "node_modules"}:
            continue
        venv = cfg.parent
        python = venv / "bin" / "python"
        rc, out, _ = run([str(python), "-c", "import sys;print(sys.prefix)"]) if python.exists() else (None, "", "")
        venvs.append({"path": str(venv), "python_ok": rc == 0, "prefix_matches": out == str(venv)})
    return venvs


def systemd_units(slug: str, code_path: Path) -> list[dict[str, Any]]:
    """Inspect matching user services (explicit runtime opt-in only)."""
    units_dir = Path.home() / ".config" / "systemd" / "user"
    hits: list[dict[str, Any]] = []
    if not units_dir.is_dir():
        return hits
    for unit in sorted(units_dir.glob("*.service")):
        text = unit.read_text(errors="ignore")
        if str(code_path) in text or unit.name.startswith(slug):
            _, active, _ = run(["systemctl", "--user", "is-active", unit.name])
            hits.append({"unit": unit.name, "active": active})
    return hits


def status_for(
    proj_dir: Path,
    *,
    runtime_checks: bool = False,
    now: datetime.datetime | None = None,
    stale_days: int = DEFAULT_STALE_DAYS,
) -> dict[str, Any]:
    slug = proj_dir.name
    summary = proj_dir / "_summary.md"
    out: dict[str, Any] = {
        "slug": slug,
        "runtime_checks": runtime_checks,
        "missing_files": [f for f in REQUIRED_FILES if not (proj_dir / f).exists()],
    }
    if not summary.exists():
        out["code_state"] = {
            "record": {"status": "absent", "present": False, "record_valid": False, "fields": {}, "warnings": []},
            "current_observation": None,
            "comparison": {"status": "unknown", "metadata_match": None, "reason": "summary missing"},
        }
        return out

    try:
        text = summary.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        out["summary_error"] = str(exc)
        out["code_state"] = {
            "record": {"status": "invalid", "present": True, "record_valid": False, "fields": {}, "warnings": [str(exc)]},
            "current_observation": None,
            "comparison": {"status": "unverified", "metadata_match": None, "reason": "summary unreadable"},
        }
        return out

    fm, fm_errors = _parse_frontmatter(text)
    out["status"] = fm.get("status")
    summary_modified = parse_iso(fm.get("modified"))
    out["summary_modified"] = summary_modified.isoformat() if summary_modified else None

    location = resolve_code_location(text)
    out["code_location"] = location.get("path")
    out["code_exists"] = location.get("status") in {"ok", "non-directory"}
    out["code_location_status"] = location.get("status")
    out["code_locations"] = location.get("locations", [])
    if location.get("error"):
        out["code_location_error"] = location["error"]

    record = parse_code_state(fm, frontmatter_errors=fm_errors, now=now)
    current: dict[str, Any] | None = None
    if location.get("status") == "ok":
        code_path = Path(location.get("resolved", location["path"]))
        git = git_info(code_path)
        out["git"] = git
        # Preserve the old field but explicitly mark this as a document-date
        # heuristic, not an implementation/state verification.
        last_date = parse_iso(git.get("last_commit_date"))
        if last_date and summary_modified:
            days = (last_date - summary_modified).days
            out["summary_days_behind_code"] = days
            out["summary_date_comparison"] = {
                "days": days,
                "kind": "document-date heuristic",
                "implementation_verified": False,
            }
        current = current_code_observation(code_path, now=now, git=git)
        if runtime_checks:
            out["venvs"] = venv_info(code_path)
            out["systemd_units"] = systemd_units(slug, code_path)
        else:
            # Keep the historical keys without implying that an empty list was
            # observed. None means the runtime check was deliberately skipped.
            out["venvs"] = None
            out["systemd_units"] = None
    out["code_state"] = {
        "record": record,
        "current_observation": current,
        "comparison": compare_code_state(record, current, now=now, stale_days=stale_days),
    }
    return out


def _project_dirs(projects_root: Path) -> list[Path]:
    return sorted(
        p for p in projects_root.iterdir()
        if p.is_dir() and not p.is_symlink() and not p.name.startswith((".", "_"))
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only KB project status snapshot.")
    parser.add_argument("--kb-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("slug", nargs="?", help="single project slug; omit with --all")
    parser.add_argument("--all", action="store_true", help="report every project")
    parser.add_argument(
        "--runtime-checks",
        action="store_true",
        help="opt in to Python-venv interpreter and systemd --user diagnostics",
    )
    parser.add_argument("--stale-days", type=int, default=DEFAULT_STALE_DAYS)
    args = parser.parse_args(argv)

    projects_root = Path(args.kb_root).expanduser().resolve() / "projects"
    if not projects_root.is_dir():
        print(json.dumps({"error": f"projects dir not found: {projects_root}"}))
        return 2
    if args.all and args.slug:
        print(json.dumps({"error": "provide either --all or a project slug, not both"}))
        return 2
    if args.stale_days < 0:
        print(json.dumps({"error": "--stale-days must be non-negative"}))
        return 2

    if args.all or not args.slug:
        dirs = _project_dirs(projects_root)
    else:
        if not SLUG_RE.fullmatch(args.slug):
            print(json.dumps({"error": f"invalid project slug: {args.slug}"}))
            return 2
        target = projects_root / args.slug
        try:
            resolved_target = target.resolve()
            resolved_target.relative_to(projects_root)
        except (OSError, RuntimeError, ValueError):
            print(json.dumps({"error": f"project path escapes projects dir: {args.slug}"}))
            return 2
        if target.is_symlink() or not target.is_dir():
            print(json.dumps({"error": f"no such project: {args.slug}"}))
            return 2
        dirs = [target]

    print(json.dumps({
        "projects": [
            status_for(d, runtime_checks=args.runtime_checks, stale_days=args.stale_days)
            for d in dirs
        ]
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

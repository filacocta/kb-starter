"""Portable, fail-closed configuration and filesystem helpers for KB CLIs.

The command-line helpers in :mod:`scripts` are intentionally usable from a
checkout, an installed copy, or another agent harness.  This module is the
single place where they discover a knowledge-base root.  In particular, there
is no historical guessed-vault fallback: selecting a vault implicitly is
unsafe for a personal knowledge base and makes a starter impossible to share.

Resolution order (the first applicable item wins):

* ``--kb-root`` supplied by a caller (passed as ``explicit`` here);
* a non-empty ``KB_ROOT`` environment variable;
* a non-empty ``KB_CONFIG`` or ``KB_CONFIG_PATH`` environment variable;
* the selected agent's default config when ``KB_AGENT`` is set;
* one unambiguous Codex/Claude default config.

The parser reads the selected root setting.  Config files are not YAML documents and
are never printed by this module.  Duplicate root settings, unreadable paths,
and more than one discovered default config fail closed.
"""

from __future__ import annotations

import os
import json
import re
import stat
from pathlib import Path
from typing import Iterable, Mapping


class KBConfigError(RuntimeError):
    """A missing, invalid, or ambiguous KB configuration."""


SUPPORTED_AGENTS = ("claude", "codex")
_TOP_LEVEL_SEPARATORS = {"/", "\\"}


def _env(environ: Mapping[str, str] | None, key: str) -> str | None:
    source = os.environ if environ is None else environ
    return source.get(key)


def _nonempty(value: str | os.PathLike[str] | None) -> str | None:
    if value is None:
        return None
    text = os.fspath(value)
    return text if text.strip() else None


def _expand_path(value: str | os.PathLike[str], base: Path | None = None) -> Path:
    path = Path(os.path.expanduser(os.fspath(value)))
    if not path.is_absolute():
        path = (base if base is not None else Path.cwd()) / path
    # ``resolve(strict=False)`` gives callers a stable absolute path while the
    # explicit symlink check below prevents a scan from escaping the chosen
    # tree.  It also leaves a useful path in a missing-root error.
    return path.resolve(strict=False)


def _lexical_absolute(value: str | os.PathLike[str], base: Path | None = None) -> Path:
    """Make an absolute path without resolving symlinks or ``..`` components."""
    path = Path(os.path.expanduser(os.fspath(value)))
    if not path.is_absolute():
        path = (base if base is not None else Path.cwd()) / path
    return path.absolute()


def _reject_symlink_components(path: Path, *, allow_missing_final: bool = False) -> None:
    """Reject symlink path components before reading a configured path."""
    if not path.is_absolute():
        raise KBConfigError("path must be absolute")
    cursor = Path(path.anchor)
    parts = path.parts
    for index, part in enumerate(parts):
        if index == 0 and part == path.anchor:
            continue
        cursor /= part
        final = index == len(parts) - 1
        try:
            info = os.lstat(cursor)
        except FileNotFoundError:
            if final and allow_missing_final:
                return
            raise
        if stat.S_ISLNK(info.st_mode):
            raise KBConfigError(f"symlink path component is not allowed: {str(cursor)}")
        if not final and not stat.S_ISDIR(info.st_mode):
            raise KBConfigError(f"path component is not a directory: {str(cursor)}")


def _validated_root(value: str | os.PathLike[str], *, source: str,
                    base: Path | None = None) -> Path:
    """Expand and validate one explicit root without falling through."""
    raw = _nonempty(value)
    if raw is None:
        raise KBConfigError(f"{source} is empty; set an explicit KB_ROOT or config path")
    try:
        requested = _lexical_absolute(raw, base)
        # Preserve the lexical path for the symlink check.  A root itself (or
        # a parent) reached through a symlink is not a safe scan boundary.
        requested = requested.absolute()
        _reject_symlink_components(requested)
        root = requested.resolve(strict=True)
        if root != requested:
            raise KBConfigError("KB root must not contain symlinks")
        info = os.stat(requested, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode):
            raise KBConfigError(f"{source} is not a directory: {str(requested)}")
        # An explicit root must be readable and searchable.  ``os.access`` is
        # not the entire authorization model, but catches ordinary mistakes
        # without opening any notes or exposing their contents.
        if not os.access(requested, os.R_OK | os.X_OK):
            raise KBConfigError(f"{source} is not readable: {str(requested)}")
    except KBConfigError:
        raise
    except (OSError, RuntimeError) as exc:
        raise KBConfigError(
            f"{source} cannot be used as a KB directory: {str(_expand_path(raw, base))}"
        ) from exc
    return requested


def _unquote_config_value(value: str) -> str:
    """Strip a simple quoted config value and an unquoted trailing comment."""
    value = value.strip()
    if not value:
        return ""
    if value[0] == '"':
        try:
            decoded, end = json.JSONDecoder().raw_decode(value)
        except ValueError as exc:
            raise KBConfigError("invalid double-quoted KB config value") from exc
        tail = value[end:].strip()
        if tail and not tail.startswith("#"):
            raise KBConfigError("unexpected text after quoted KB config value")
        return decoded
    if value[0] in {"'", '"'}:
        quote = value[0]
        closing = value.find(quote, 1)
        tail = value[closing + 1:].strip() if closing >= 0 else ""
        if closing < 0 or (tail and not tail.startswith("#")):
            raise KBConfigError("KB_ROOT config value has an unterminated quote")
        return value[1:closing].strip()
    # Config templates use shell-ish comments.  A ``#`` in a quoted path was
    # handled above; in an unquoted path it is conventionally a comment.
    return value.split(" #", 1)[0].rstrip()


def _read_config_root(config_path: Path, key: str = "KB_ROOT") -> Path:
    """Read exactly one usable KB_ROOT setting from a config file."""
    try:
        _reject_symlink_components(config_path)
        info = os.stat(config_path, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode):
            raise KBConfigError(f"KB config is not a regular file: {str(config_path)}")
        if not os.access(config_path, os.R_OK):
            raise KBConfigError(f"KB config is not readable: {str(config_path)}")
        text = read_regular_text(config_path)
    except KBConfigError:
        raise
    except (OSError, UnicodeError) as exc:
        raise KBConfigError(f"KB config cannot be read: {str(config_path)}") from exc

    values: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^\s*" + re.escape(key) + r"\s*[:=]\s*(.*?)\s*$", raw_line)
        if not match:
            continue
        value = _unquote_config_value(match.group(1))
        if not value:
            raise KBConfigError(f"KB config has an empty {key}: {str(config_path)}")
        values.append(value)

    if not values:
        raise KBConfigError(
            f"KB config has no usable {key} setting: {str(config_path)}"
        )
    if len(values) != 1:
        raise KBConfigError(
            f"KB config has ambiguous duplicate {key} settings: {str(config_path)}"
        )
    return _validated_root(values[0], source=f"{key} in config",
                           base=config_path.parent)


def _config_path(value: str | os.PathLike[str], *, source: str) -> Path:
    raw = _nonempty(value)
    if raw is None:
        raise KBConfigError(f"{source} is empty")
    # Keep the lexical path until the no-symlink walk has completed.  Calling
    # ``resolve`` first would hide a symlinked config file or parent.
    path = _lexical_absolute(raw)
    try:
        _reject_symlink_components(path)
    except FileNotFoundError as exc:
        raise KBConfigError(f"{source} does not exist: {str(path)}") from exc
    except KBConfigError:
        raise
    except OSError as exc:
        raise KBConfigError(f"{source} cannot be used: {str(path)}") from exc
    return path


def _default_config_paths(agent: str | None, environ: Mapping[str, str] | None) -> list[Path]:
    if agent is not None and agent not in SUPPORTED_AGENTS:
        raise KBConfigError(
            f"unsupported KB_AGENT {agent!r}; choose 'claude' or 'codex'"
        )

    home = Path.home()
    codex_home = _nonempty(_env(environ, "CODEX_HOME"))
    claude_home = _nonempty(_env(environ, "CLAUDE_HOME"))
    homes = {
        "codex": _lexical_absolute(codex_home, home) if codex_home else home / ".codex",
        "claude": _lexical_absolute(claude_home, home) if claude_home else home / ".claude",
    }
    selected = (agent,) if agent else ("codex", "claude")
    return [homes[name] / "kb-config.md" for name in selected]


def resolve_kb_root(explicit: str | os.PathLike[str] | None = None, *,
                    config: str | os.PathLike[str] | None = None,
                    agent: str | None = None,
                    domain: str | None = None,
                    environ: Mapping[str, str] | None = None) -> Path:
    """Resolve and validate the KB root using the portable fail-closed order.

    ``explicit`` is used for a CLI ``--kb-root`` option.  ``config`` is useful
    to callers that already parsed a config path; normal CLIs should leave it
    unset and let ``KB_CONFIG``/``KB_CONFIG_PATH`` be read here.  An explicit
    but invalid setting raises :class:`KBConfigError` rather than falling back
    to another possible vault.
    """
    env = os.environ if environ is None else environ
    if domain not in (None, "personal", "work"):
        raise KBConfigError("domain must be personal or work")
    root_key = f"KB_{domain.upper()}_ROOT" if domain else "KB_ROOT"
    selected_agent = agent if agent is not None else _nonempty(_env(env, "KB_AGENT"))
    if selected_agent is not None:
        selected_agent = selected_agent.strip().lower()
        if selected_agent not in SUPPORTED_AGENTS:
            raise KBConfigError(
                f"unsupported KB_AGENT {selected_agent!r}; choose 'claude' or 'codex'"
            )

    if _nonempty(explicit) is not None:
        root = _validated_root(explicit, source="explicit KB root")
        bound = _nonempty(_env(env, root_key))
        if domain and bound and root != _validated_root(bound, source=root_key):
            raise KBConfigError("explicit root does not match the selected domain binding")
        return root

    env_root = _nonempty(_env(env, root_key))
    if env_root is not None:
        return _validated_root(env_root, source="KB_ROOT")

    config_values = [
        value for value in (_env(env, "KB_CONFIG"), _env(env, "KB_CONFIG_PATH"))
        if _nonempty(value) is not None
    ]
    if config is not None:
        config_values.append(os.fspath(config))
    if config_values:
        config_paths = [_config_path(value, source="KB_CONFIG") for value in config_values]
        unique_paths: list[Path] = []
        for path in config_paths:
            if path not in unique_paths:
                unique_paths.append(path)
        if len(unique_paths) > 1:
            raise KBConfigError(
                "KB_CONFIG and KB_CONFIG_PATH name different files; choose one explicitly"
            )
        return _read_config_root(unique_paths[0], root_key)

    candidates = _default_config_paths(selected_agent, env)
    existing: list[Path] = []
    for candidate in candidates:
        try:
            if candidate.exists():
                existing.append(candidate)
        except OSError as exc:
            raise KBConfigError(f"cannot inspect supported KB config: {str(candidate)}") from exc
    if len(existing) > 1:
        names = ", ".join(str(path) for path in existing)
        raise KBConfigError(
            f"ambiguous KB config; multiple supported configs exist ({names}); "
            "set KB_AGENT or KB_CONFIG explicitly"
        )
    if len(existing) == 1:
        return _read_config_root(existing[0], root_key)

    if domain:
        raise KBConfigError(f"no {domain} KB root configured; set {root_key} or pass --kb-root")
    raise KBConfigError(
        "no KB root configured; set KB_ROOT or KB_CONFIG/KB_CONFIG_PATH "
        "(or install a selected agent config)"
    )


def validate_topic_slug(topic: str, root: Path | None = None) -> str:
    """Validate one topic slug and optionally require its directory to exist."""
    if not isinstance(topic, str) or not topic.strip():
        raise KBConfigError("topic must be a non-empty slug")
    slug = topic.strip()
    if slug in {".", ".."} or "\x00" in slug or any(separator in slug for separator in _TOP_LEVEL_SEPARATORS):
        raise KBConfigError("topic must be a single path-safe slug")
    if Path(slug).name != slug or slug.startswith("."):
        raise KBConfigError("topic must be a single path-safe slug")
    if root is not None:
        topic_dir = root / "topics" / slug
        try:
            _reject_symlink_components(topic_dir)
            info = os.stat(topic_dir, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise KBConfigError(f"topic does not exist: {slug}") from exc
        except OSError as exc:
            raise KBConfigError(f"topic cannot be inspected: {slug}") from exc
        if not stat.S_ISDIR(info.st_mode):
            raise KBConfigError(f"topic is not a directory: {slug}")
    return slug


def _safe_base(root: Path, relative: str | os.PathLike[str]) -> Path:
    value = Path(relative)
    if value.is_absolute() or ".." in value.parts:
        raise KBConfigError("scan path escapes the KB root")
    base = root / value
    _reject_symlink_components(base)
    info = os.stat(base, follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode):
        raise KBConfigError(f"scan path is not a directory: {value.as_posix()}")
    return base


def iter_markdown(root: Path, relative_dirs: Iterable[str | os.PathLike[str]] | None = None,
                  *, max_entries: int | None = None, exclude_dirs: Iterable[str] = ()):
    """Yield regular Markdown files without traversing symlinked paths.

    Missing optional subdirectories are ignored, which keeps the index and
    audit CLIs useful for an empty starter root.  A supplied existing symlink
    or non-directory is rejected so a typo cannot broaden a scan silently.
    """
    root = _lexical_absolute(root)
    _reject_symlink_components(root)
    try:
        root_info = os.stat(root, follow_symlinks=False)
    except OSError as exc:
        raise KBConfigError(f"scan root cannot be inspected: {str(root)}") from exc
    if not stat.S_ISDIR(root_info.st_mode):
        raise KBConfigError(f"scan root is not a directory: {str(root)}")
    if relative_dirs is None:
        bases = [root]
    else:
        bases = []
        for relative in relative_dirs:
            value = Path(relative)
            if value.is_absolute() or ".." in value.parts:
                raise KBConfigError("scan path escapes the KB root")
            candidate = root / value
            try:
                bases.append(_safe_base(root, value))
            except FileNotFoundError:
                continue

    seen_bases: set[Path] = set()
    excluded = set(exclude_dirs)
    entries = 0

    def scan_error(error):
        raise KBConfigError(f"directory traversal failed: {error}") from error

    for base in bases:
        if base in seen_bases:
            continue
        seen_bases.add(base)
        for dirpath, dirnames, filenames in os.walk(base, topdown=True, followlinks=False, onerror=scan_error):
            entries += len(dirnames) + len(filenames)
            if max_entries is not None and entries > max_entries:
                raise KBConfigError("scan entry limit reached")
            current = Path(dirpath)
            safe_dirs: list[str] = []
            for dirname in sorted(dirnames):
                if dirname.startswith(".") or dirname in excluded:
                    continue
                candidate = current / dirname
                try:
                    info = os.lstat(candidate)
                except OSError as exc:
                    scan_error(exc)
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                    continue
                safe_dirs.append(dirname)
            dirnames[:] = safe_dirs
            for filename in sorted(filenames):
                if not filename.endswith(".md"):
                    continue
                candidate = current / filename
                try:
                    info = os.lstat(candidate)
                except OSError as exc:
                    scan_error(exc)
                if stat.S_ISREG(info.st_mode):
                    yield candidate


def read_regular_bytes(path: Path | str, *, max_bytes: int | None = None) -> bytes:
    """Read bounded bytes from one regular file without following symlinks."""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = candidate.absolute()
    _reject_symlink_components(candidate)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise KBConfigError("platform lacks O_NOFOLLOW; refusing KB read")
    flags = os.O_RDONLY | nofollow
    descriptor = os.open(candidate, flags)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise KBConfigError(f"file is not regular: {str(candidate)}")
        if info.st_nlink != 1:
            raise KBConfigError(f"file has multiple hard links: {str(candidate)}")
        if max_bytes is not None and info.st_size > max_bytes:
            raise KBConfigError(f"file exceeds the read limit: {str(candidate)}")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            raw = handle.read() if max_bytes is None else handle.read(max_bytes + 1)
        if max_bytes is not None and len(raw) > max_bytes:
            raise KBConfigError(f"file grew beyond the read limit: {str(candidate)}")
        return raw
    finally:
        if descriptor != -1:
            os.close(descriptor)


def read_regular_text(path: Path | str, *, max_bytes: int | None = None) -> str:
    try:
        return read_regular_bytes(path, max_bytes=max_bytes).decode("utf-8")
    except UnicodeError as exc:
        raise KBConfigError(f"file is not valid UTF-8: {str(Path(path))}") from exc


def atomic_write_text(path: Path | str, text: str) -> None:
    """Atomically replace a regular file without following a symlink.

    This helper is intended for generated cache files.  Content-note writes
    must use the stronger shared ``knowledgebase/_tools/kb-write-guard.py``
    lock and atomic writer instead.
    """
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = candidate.absolute()
    _reject_symlink_components(candidate.parent)
    existing: os.stat_result | None = None
    try:
        info = os.lstat(candidate)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise KBConfigError(f"cache target is not a regular file: {str(candidate)}")
        existing = info
    except FileNotFoundError:
        pass
    import tempfile

    descriptor, temporary = tempfile.mkstemp(prefix=".kb-cache-", dir=str(candidate.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            descriptor = -1
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        _reject_symlink_components(candidate.parent)
        try:
            current = os.lstat(candidate)
        except FileNotFoundError:
            current = None
        if existing is not None:
            if current is None or (current.st_dev, current.st_ino) != (existing.st_dev, existing.st_ino):
                raise KBConfigError("cache target changed while writing")
        elif current is not None:
            raise KBConfigError("cache target appeared while writing")
        os.replace(temporary, candidate)
        temporary = ""
    finally:
        if descriptor != -1:
            os.close(descriptor)
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

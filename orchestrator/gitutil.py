"""Read-only Git helpers and change attribution via SHA-256 manifests.

The orchestrator never mutates Git state. Everything here only *reads* Git and
the working tree. Change attribution uses content hashes so that a repository
with no commits (unborn branch, where ``git diff`` is empty) is still handled
correctly.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

MAX_HASH_BYTES = 20 * 1024 * 1024


def run_git(
    repo_root: str,
    args: List[str],
    timeout: int = 60,
) -> Tuple[int, str, str]:
    """Run a read-only git command. Returns (returncode, stdout, stderr)."""
    cmd = ["git", "-C", repo_root, *args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError:
        return 127, "", "git executable not found"
    except subprocess.TimeoutExpired:
        return 124, "", "git command timed out"


def git_head(repo_root: str) -> str:
    rc, out, _ = run_git(repo_root, ["rev-parse", "HEAD"])
    return out.strip() if rc == 0 else "(no commit)"


def git_branch(repo_root: str) -> str:
    rc, out, _ = run_git(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"])
    return out.strip() if rc == 0 else "(unknown)"


def git_toplevel(repo_root: str) -> str:
    rc, out, _ = run_git(repo_root, ["rev-parse", "--show-toplevel"])
    return out.strip() if rc == 0 else "(not a git repository)"


def git_status_porcelain(repo_root: str) -> List[str]:
    """Full porcelain status including per-file untracked entries."""
    rc, out, _ = run_git(repo_root, ["status", "--porcelain=v1", "-uall"])
    if rc != 0:
        return []
    return [line for line in out.splitlines() if line.strip()]


def git_status_scoped(repo_root: str) -> Dict[str, List[str]]:
    """Parse git status and keep only entries inside ``repo_root``.

    Because the Git top-level may be a parent of ``repo_root`` (as with the
    Sentinel repository), entries reported as ``../x`` belong to other trees and
    are excluded.
    """
    result: Dict[str, List[str]] = {
        "modified": [],
        "deleted": [],
        "added": [],
        "renamed": [],
        "untracked": [],
        "other": [],
    }
    for line in git_status_porcelain(repo_root):
        code = line[:2]
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        if path.startswith("../") or path == "..":
            continue
        if code == "??":
            result["untracked"].append(path)
        elif "D" in code:
            result["deleted"].append(path)
        elif "A" in code:
            result["added"].append(path)
        elif "R" in code:
            result["renamed"].append(path)
        elif "M" in code:
            result["modified"].append(path)
        else:
            result["other"].append(path)
    return result


def _iter_files(repo_root: str, ignore_dirs: List[str], ignore_globs: List[str]):
    ignore = set(ignore_dirs)
    root = Path(repo_root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d
            for d in dirnames
            if d not in ignore and not _matches(d + "/", ignore_globs)
        ]
        for name in filenames:
            full = Path(dirpath) / name
            try:
                rel = full.relative_to(root).as_posix()
            except ValueError:
                continue
            if _matches(rel, ignore_globs):
                continue
            yield full


def _matches(rel_path: str, globs: List[str]) -> bool:
    import fnmatch

    for pattern in globs:
        pat = pattern.replace("\\", "/")
        if fnmatch.fnmatchcase(rel_path, pat) or fnmatch.fnmatchcase(rel_path, pat.rstrip("/") + "/*"):
            return True
    return False


def snapshot_manifest(
    repo_root: str,
    ignore_dirs: Optional[List[str]] = None,
    ignore_globs: Optional[List[str]] = None,
) -> Dict[str, str]:
    """Return a mapping of repo-relative POSIX path -> SHA-256 (or size marker)."""
    ignore_dirs = ignore_dirs or [".git"]
    ignore_globs = ignore_globs or []
    root = Path(repo_root)
    manifest: Dict[str, str] = {}
    for full in _iter_files(repo_root, ignore_dirs, ignore_globs):
        try:
            rel = full.relative_to(root).as_posix()
        except ValueError:
            continue
        try:
            size = full.stat().st_size
            if size > MAX_HASH_BYTES:
                manifest[rel] = f"<large:{size}>"
                continue
            digest = hashlib.sha256()
            with full.open("rb") as handle:
                for chunk in iter(lambda: handle.read(65536), b""):
                    digest.update(chunk)
            manifest[rel] = digest.hexdigest()
        except OSError:
            manifest[rel] = "<unreadable>"
    return manifest


def diff_manifests(
    before: Dict[str, str],
    after: Dict[str, str],
) -> Dict[str, List[str]]:
    """Compare two manifests. ``modified`` means same path, different hash."""
    before_keys = set(before)
    after_keys = set(after)
    added = sorted(after_keys - before_keys)
    removed = sorted(before_keys - after_keys)
    modified = sorted(k for k in (before_keys & after_keys) if before[k] != after[k])
    return {"added": added, "removed": removed, "modified": modified}


def is_clean(status: Dict[str, List[str]]) -> bool:
    return not any(status.get(key) for key in status)

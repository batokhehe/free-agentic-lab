"""Safe subprocess execution for the OpenCode CLI and configured commands.

Guarantees:
  * No ``shell=True`` for the OpenCode invocation (argument list only).
  * Hard wall-clock timeout; on expiry the whole process tree is killed.
  * stdout/stderr are captured separately and UTF-8 decoded with replacement.
  * Exit codes are always surfaced; the orchestrator never assumes success.

Only documented ``opencode run`` flags are used (see Phase 0 audit): ``--model``,
``--dir``, ``--agent``, ``--format``. ``--auto`` is intentionally never passed
because it auto-approves permissions, which would bypass approval gates.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

Command = Union[str, Sequence[str]]

# Text signals that a provider reached its request/quota limit rather than a
# plain code failure. Only consulted when a run already failed.
_PROVIDER_LIMIT_PATTERNS = (
    "rate limit",
    "rate-limit",
    "ratelimit",
    "too many requests",
    "insufficient_quota",
    "resource_exhausted",
    "quota",
    "usage limit",
    "overloaded",
    "capacity",
    "temporarily unavailable",
    "no capacity",
    "try again later",
    "429",
)


def detect_provider_limit(result: "ProcessResult") -> Optional[str]:
    """Return the matched signal if ``result`` failed with a provider limit.

    Returns ``None`` when the run was not actually a provider limit (including
    any successful run, even if its output mentions these words in prose).
    """
    if result.ok:
        return None
    combined = f"{result.stdout}\n{result.stderr}".lower()
    for signal in _PROVIDER_LIMIT_PATTERNS:
        if signal in combined:
            return signal
    return None


@dataclass
class ProcessResult:
    command: str
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    duration_seconds: float = 0.0
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.error

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command": self.command,
            "returncode": self.returncode,
            "timed_out": self.timed_out,
            "duration_seconds": round(self.duration_seconds, 3),
            "error": self.error,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


def opencode_available(command: str = "opencode") -> bool:
    return resolve_command(command) is not None


def resolve_command(command: str) -> Optional[str]:
    """Resolve a command name to an absolute path on PATH.

    On Windows, ``shutil.which`` may return a ``.cmd``/``.ps1`` shim; a ``.CMD``
    shim still cannot be executed by ``CreateProcess`` without ``cmd.exe`` (handled
    in :func:`_run_popen`), and a ``.ps1`` shim is not usable at all, so callers
    should prefer a resolved absolute path here.
    """
    if not command:
        return None
    if os.path.isabs(command) and os.path.isfile(command):
        return command
    found = shutil.which(command)
    if found and found.lower().endswith((".cmd", ".bat")):
        unwrapped = _unwrap_windows_shim(found)
        if unwrapped:
            return unwrapped
    return found


def _unwrap_windows_shim(shim_path: str) -> Optional[str]:
    """Resolve an npm ``.cmd`` shim to the real ``.exe`` it invokes.

    Launching the ``.cmd`` requires ``cmd.exe``, which corrupts multi-line
    arguments (e.g. a long model prompt). Pointing at the underlying ``.exe``
    lets ``CreateProcess`` pass the argument verbatim.
    """
    try:
        text = Path(shim_path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    match = re.search(r'"?([^"\r\n]*node_modules[\\/][^"\r\n]*\.exe)"?', text)
    if not match:
        return None
    candidate = re.sub(r"(?i)%~?dp0%?", "", match.group(1)).lstrip("\\/")
    full = Path(shim_path).parent / candidate
    return str(full) if full.is_file() else None


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=30,
            )
        else:  # pragma: no cover - non-Windows fallback
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _run_popen(
    cmd: Union[List[str], str],
    cwd: Optional[str],
    timeout: Optional[float],
    shell: bool,
    env: Optional[Dict[str, str]] = None,
) -> ProcessResult:
    if not shell and isinstance(cmd, list) and os.name == "nt":
        # .cmd/.bat shims (e.g. npm global opencode) require cmd.exe to launch.
        if str(cmd[0]).lower().endswith((".cmd", ".bat")):
            cmd = [os.environ.get("ComSpec", "cmd.exe"), "/c", *cmd]
    display = cmd if isinstance(cmd, str) else " ".join(cmd)
    started = time.monotonic()
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    child_env = None
    if env:
        child_env = dict(os.environ)
        child_env.update(env)
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=shell,
            creationflags=creationflags,
            env=child_env,
        )
    except FileNotFoundError as exc:
        return ProcessResult(
            command=display,
            returncode=127,
            stdout="",
            stderr="",
            duration_seconds=time.monotonic() - started,
            error=f"executable not found: {exc}",
        )
    except OSError as exc:
        return ProcessResult(
            command=display,
            returncode=126,
            stdout="",
            stderr="",
            duration_seconds=time.monotonic() - started,
            error=f"failed to start process: {exc}",
        )

    timed_out = False
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            out, err = "", ""
        err = (err or "") + f"\n[orchestrator] timeout after {timeout}s; process tree killed."

    return ProcessResult(
        command=display,
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=out or "",
        stderr=err or "",
        timed_out=timed_out,
        duration_seconds=time.monotonic() - started,
    )


def build_opencode_command(
    prompt: str,
    repo_root: str,
    opencode_config: Dict[str, Any],
) -> List[str]:
    """Build a documented, non-interactive ``opencode run`` argument list."""
    raw_command = str(opencode_config.get("command", "opencode"))
    command = resolve_command(raw_command) or raw_command
    args: List[str] = [command, "run"]
    model = opencode_config.get("model")
    if model:
        args += ["--model", str(model)]
    agent = opencode_config.get("agent")
    if agent:
        args += ["--agent", str(agent)]
    args += ["--dir", repo_root]
    for extra in opencode_config.get("extra_args", []) or []:
        args.append(str(extra))
    args.append(prompt)
    return args


def run_opencode(
    prompt: str,
    repo_root: str,
    opencode_config: Dict[str, Any],
    timeout: Optional[float] = None,
    cwd: Optional[str] = None,
) -> ProcessResult:
    """Run ``opencode run`` in a non-interactive session and capture everything.

    If ``opencode_config`` declares an ``agent_config`` path, it is passed to
    the child process via ``OPENCODE_CONFIG`` so agent/permission controls are
    applied regardless of which repo is the target. ``--auto`` is never passed.
    """
    if timeout is None:
        timeout = float(opencode_config.get("timeout_seconds", 900))
    args = build_opencode_command(prompt, repo_root, opencode_config)
    env = None
    agent_config = opencode_config.get("agent_config")
    if agent_config and os.path.isfile(str(agent_config)):
        env = {"OPENCODE_CONFIG": str(agent_config)}
    return _run_popen(args, cwd=cwd or repo_root, timeout=timeout, shell=False, env=env)


def run_command(
    command: Command,
    cwd: Optional[str] = None,
    timeout: Optional[float] = 600,
) -> ProcessResult:
    """Run a configured test/verification command.

    A list is executed without a shell; a string is executed through the platform
    shell. Callers must validate string commands with
    :func:`orchestrator.policy.check_command_safety` beforehand.
    """
    shell = isinstance(command, str)
    return _run_popen(command, cwd=cwd, timeout=timeout, shell=shell)

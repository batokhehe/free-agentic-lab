"""Repository boundaries, scope, command-safety and secret handling.

This module is the *out-of-prompt* safety layer. It never trusts the model:
every command the orchestrator may run, every path a task may touch, and every
change detected after a run is validated here, in Python, regardless of what the
model prompt or the model output claims.

Policy categories:
  * ``ok``       - allowed to run without extra approval.
  * ``approval`` - allowed only with explicit human approval.
  * ``deny``     - never allowed by the orchestrator.
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

ALLOWED_MODES = {"read_only", "implement"}

# --- secret detection -------------------------------------------------------

_SECRET_KEY_RE = re.compile(
    r"(?i)(api[_-]?key|secret|passwd|password|token|credential|"
    r"private[_-]?key|access[_-]?key|client[_-]?secret|authorization)"
)
_SECRET_VALUE_RES = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(
        r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*[\"']?[A-Za-z0-9_\-/+]{12,}"
    ),
]

# --- command safety ---------------------------------------------------------
# Each entry: (compiled regex, category, human reason).
_DENY: List[Tuple[re.Pattern, str]] = [
    (
        re.compile(
            r"(?i)(^|[^\w])git\s+("
            r"commit|push|reset|clean|stash|checkout|switch|restore|rebase|merge|"
            r"tag|remote|cherry-pick|revert|filter-branch|gc|prune|update-index)\b"
        ),
        "forbidden/destructive git operation",
    ),
    (
        re.compile(r"(?i)(^|[^\w])git\s+(add|stage)\b"),
        "modifying the git index/staging area",
    ),
    (
        re.compile(r"(?i)(^|[^\w])rm\s+-[a-z]*r[a-z]*f|(^|[^\w])rm\s+-[a-z]*f[a-z]*r"),
        "recursive/forced file deletion",
    ),
    (
        re.compile(r"(?i)Remove-Item\b.*(-Recurse|-Force)", re.I),
        "recursive/forced file deletion (PowerShell)",
    ),
    (
        re.compile(r"(?i)(^|[^\w])(del|erase)\s+/[fsq]|(^|[^\w])rd\s+/s|(^|[^\w])rmdir\s+/s"),
        "forced/recursive directory deletion (cmd)",
    ),
    (
        re.compile(r"(?i)(^|[^\w])(format|diskpart|mkfs)\b"),
        "disk/format operation",
    ),
    (
        re.compile(r"(?i)\b(DROP\s+(TABLE|DATABASE)|TRUNCATE\s+TABLE)\b"),
        "destructive database operation",
    ),
    (
        re.compile(
            r"(?i)(^|[^\w])(nmap|masscan|sqlmap|nikto|metasploit|msfconsole|"
            r"msfvenom|hydra|gobuster|ffuf|dirb|dirbuster|wpscan|nuclei|"
            r"responder|aircrack-ng|kismet|zaproxy)\b"
        ),
        "active scanning/offensive-security tool",
    ),
    (
        re.compile(r"(?i)(^|[^\w])(nc|ncat|netcat)\s+-"),
        "raw network connection (netcat)",
    ),
    (
        re.compile(
            r"(?i)(docker\s+(push|deploy)|kubectl\s+(apply|delete|replace)|"
            r"terraform\s+(apply|destroy)|ansible-playbook|helms?(\s|$)|"
            r"(npm|pnpm|yarn)\s+publish|gh\s+release|twine\s+upload)"
        ),
        "deployment/publishing operation",
    ),
    (
        re.compile(
            r"(?i)(chmod\s+777|setenforce\s+0|ufw\s+disable|"
            r"netsh\s+advfirewall.*(off|disable)|"
            r"Set-NetFirewallProfile.*-Enabled\s+False)"
        ),
        "weakening host/security configuration",
    ),
    (
        re.compile(r"(?i)(^|[^\w])shutdown\b|(^|[^\w])Stop-Computer\b|(^|[^\w])Restart-Computer\b"),
        "host power operation",
    ),
]

_APPROVAL: List[Tuple[re.Pattern, str]] = [
    (
        re.compile(
            r"(?i)(^|[^\w])(npm|pnpm|yarn)\s+(install|add|remove|uninstall)\b|"
            r"(^|[^\w])pip\s+(install|uninstall)\b|"
            r"(^|[^\w])go\s+get\b|(^|[^\w])composer\s+(require|install)\b|"
            r"(^|[^\w])apt(-get)?\s+(install|remove)\b"
        ),
        "dependency installation/change",
    ),
    (
        re.compile(
            r"(?i)(migrate|migration|goose|prisma\s+migrate|artisan\s+migrate|"
            r"typeorm\s+migration|sequelize\s+db:migrate)"
        ),
        "database migration",
    ),
    (
        re.compile(r"(?i)(docker(\s+compose)?\s+(up|run|start))|kubectl\s+port-forward"),
        "starting containers/services",
    ),
]

# --- safe-command allowlist (fail-closed) -----------------------------------
# The orchestrator only ever executes commands that a human wrote into a task's
# ``tests`` list, but "written by a human" is not the same as "known safe". A
# command is treated as ``ok`` ONLY when it matches one of these conservative,
# read-only / local-verification patterns. Everything else fails closed (see
# ``check_command_safety``), so an unrecognized or ambiguous command is never
# silently treated as safe.
_SAFE: List[re.Pattern] = [
    re.compile(r"(?i)^\s*go\s+(test|vet|build|fmt|run|version|env|list|doc)\b"),
    re.compile(r"(?i)^\s*gofmt\b"),
    re.compile(r"(?i)^\s*node\s+(--version|-v|--check)\b"),
    re.compile(r"(?i)^\s*(npm|pnpm|yarn)\s+(run\s+[\w:.-]+|test|--version|-v)\b"),
    re.compile(r"(?i)^\s*(npx\s+)?(tsc|eslint|prettier)\b"),
    re.compile(
        r"(?i)^\s*python3?\s+-m\s+"
        r"(pytest|unittest|compileall|json\.tool|ruff|mypy|black|flake8)\b"
    ),
    re.compile(r"(?i)^\s*python3?\s+(--version|-V)\b"),
    re.compile(r"(?i)^\s*(pytest|ruff|mypy|black|flake8|pyright)\b"),
    re.compile(
        r"(?i)^\s*git\s+(status|rev-parse|diff|log|show|branch|tag|ls-files|"
        r"ls-tree|cat-file|hash-object|describe|for-each-ref|shortlog|blame|grep)\b"
    ),
    re.compile(r"(?i)^\s*git\s+config\s+--(get|list)\b"),
    re.compile(r"(?i)^\s*git\s+--version\b"),
    re.compile(r"(?i)^\s*(java|javac)\s+(-version|--version|-v)\b"),
    re.compile(r"(?i)^\s*(\.?[\\/])?(mvnw?|gradlew?)\b"),
    re.compile(r"(?i)^\s*(pip|pip3)\s+(--version|-V)\b"),
]

# Shell metacharacters let one command smuggle in another (chaining, piping,
# substitution, redirection). Such a command is ambiguous and is denied rather
# than pattern-matched.
_SHELL_META_RE = re.compile(r"[;&|<>`$(){}\n\r]")


def _blocked_patterns() -> List[str]:
    return [".."]


def is_within(root: str | os.PathLike[str], candidate: str | os.PathLike[str]) -> bool:
    """True if ``candidate`` resolves inside ``root`` (case-insensitive on Windows)."""
    root_abs = os.path.normcase(os.path.realpath(str(root)))
    cand_abs = os.path.normcase(os.path.realpath(str(candidate)))
    try:
        return os.path.commonpath([root_abs, cand_abs]) == root_abs
    except ValueError:
        return False


def rel_posix(root: str | os.PathLike[str], path: str | os.PathLike[str]) -> str:
    """Return a repository-relative POSIX-style path for ``path``."""
    rel = os.path.relpath(os.path.realpath(str(path)), os.path.realpath(str(root)))
    return rel.replace("\\", "/")


def matches_any(rel_path: str, patterns: Iterable[str]) -> bool:
    """fnmatch ``rel_path`` against a list of glob patterns (POSIX separators)."""
    normalized = rel_path.replace("\\", "/")
    for pattern in patterns:
        pat = pattern.replace("\\", "/")
        if fnmatch.fnmatchcase(normalized, pat):
            return True
        # Allow a directory pattern to match everything beneath it.
        if fnmatch.fnmatchcase(normalized, pat.rstrip("/") + "/*"):
            return True
    return False


def check_command_safety(
    command: str, extra_safe: Iterable[str] | None = None
) -> Dict[str, str]:
    """Classify a shell command (fail-closed).

    Returns a dict with keys ``category`` (ok/approval/deny) and ``reason``.
    Evaluation order:

    1. hard-denied patterns -> ``deny``;
    2. shell metacharacters (chaining/piping/substitution) -> ``deny``;
    3. known approval patterns -> ``approval``;
    4. explicit safe allowlist (plus ``extra_safe`` regexes) -> ``ok``;
    5. anything else -> ``deny`` ("fail-closed").

    There is deliberately no "assume safe" fallback: an unrecognized or
    ambiguous command is never classified ``ok``.
    """
    if not isinstance(command, str) or not command.strip():
        return {"category": "deny", "reason": "empty or unclassifiable command"}
    for pattern, reason in _DENY:
        if pattern.search(command):
            return {"category": "deny", "reason": reason}
    if _SHELL_META_RE.search(command):
        return {
            "category": "deny",
            "reason": "ambiguous command: contains shell metacharacters",
        }
    for pattern, reason in _APPROVAL:
        if pattern.search(command):
            return {"category": "approval", "reason": reason, "action": approval_action(command)}
    for pattern in _compile_extra_safe(extra_safe) + _SAFE:
        if pattern.search(command):
            return {"category": "ok", "reason": "recognized safe command"}
    return {
        "category": "deny",
        "reason": "unrecognized or ambiguous command (fail-closed)",
    }


def _compile_extra_safe(extra_safe: Iterable[str] | None) -> List[re.Pattern]:
    compiled: List[re.Pattern] = []
    for raw in extra_safe or []:
        try:
            compiled.append(re.compile(str(raw)))
        except re.error:
            # A malformed allowlist pattern must not widen safety: skip it.
            continue
    return compiled


def approval_action(command: str) -> str:
    """Map an approval-category command to a stable action slug.

    If the action cannot be classified, return ``"unknown"``; callers must
    treat unknown approvals as denied (fail closed).
    """
    text = command.lower()
    if re.search(r"(npm|pnpm|yarn)\s+(install|add|remove|uninstall)|pip\s+(install|uninstall)|go\s+get|composer\s+(require|install)|apt(-get)?\s+(install|remove)", text):
        return "dependency-install"
    if re.search(r"migrate|migration|goose|prisma\s+migrate|artisan\s+migrate|typeorm\s+migration|sequelize\s+db:migrate", text):
        return "db-migration"
    if re.search(r"docker(\s+compose)?\s+(up|run|start)|kubectl\s+port-forward", text):
        return "start-service"
    return "unknown"


def redact(text: str) -> str:
    """Redact likely secret values from text before it is persisted."""
    if not text:
        return ""
    result = text
    for pattern in _SECRET_VALUE_RES:
        result = pattern.sub("[REDACTED]", result)
    return result


def contains_secret_like(data: Any, _key: str = "") -> List[str]:
    """Return a list of paths where secret-like keys/values were found."""
    findings: List[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                key_str = str(key)
                if _SECRET_KEY_RE.search(key_str) and not _is_placeholder(value):
                    findings.append(f"{path}.{key_str}" if path else key_str)
                walk(value, f"{path}.{key_str}" if path else key_str)
        elif isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, f"{path}[{index}]")
        elif isinstance(node, str):
            for pattern in _SECRET_VALUE_RES:
                if pattern.search(node):
                    findings.append(path or "<value>")
                    break

    walk(data, _key)
    return findings


def _is_placeholder(value: Any) -> bool:
    if not isinstance(value, str):
        return True
    return value.strip().lower() in {"", "env", "${env}", "none", "null", "changeme"}


def _pattern_is_safe_relative(pattern: str) -> bool:
    p = pattern.replace("\\", "/")
    if p.startswith("/") or re.match(r"^[A-Za-z]:", p):
        return False
    parts = [seg for seg in p.split("/") if seg not in ("", ".")]
    return ".." not in parts


def validate_task(task: Dict[str, Any], config: Dict[str, Any]) -> List[str]:
    """Validate a task definition against policy. Returns a list of errors."""
    errors: List[str] = []

    if not task.get("id"):
        errors.append("task is missing required field 'id'")
    mode = task.get("mode", "read_only")
    if mode not in ALLOWED_MODES:
        errors.append(f"invalid mode {mode!r}; expected one of {sorted(ALLOWED_MODES)}")

    allowed = task.get("allowed_paths", []) or []
    forbidden = task.get("forbidden_paths", []) or []
    if not isinstance(allowed, list) or not isinstance(forbidden, list):
        errors.append("allowed_paths and forbidden_paths must be lists")
        allowed, forbidden = [], []

    for pattern in list(allowed) + list(forbidden):
        if not isinstance(pattern, str):
            errors.append(f"path pattern must be a string: {pattern!r}")
        elif not _pattern_is_safe_relative(pattern):
            errors.append(f"path pattern must be repository-relative: {pattern!r}")

    if mode == "read_only" and allowed:
        errors.append("read_only tasks must not declare allowed_paths")

    for pattern in allowed:
        if isinstance(pattern, str) and matches_any(pattern, list(config.get("protected_paths", []))):
            errors.append(f"allowed_paths {pattern!r} intersects a protected path")

    tests = task.get("tests", []) or []
    if not isinstance(tests, list):
        errors.append("tests must be a list")
    else:
        for index, test in enumerate(tests):
            if not isinstance(test, dict) or not test.get("command"):
                errors.append(f"tests[{index}] must be an object with a 'command'")
                continue
            verdict = check_command_safety(
                str(test["command"]), extra_safe=config.get("safe_commands")
            )
            if verdict["category"] == "deny":
                errors.append(
                    f"tests[{index}] command is forbidden: {verdict['reason']}"
                )

    secrets = contains_secret_like(task)
    if secrets:
        errors.append(f"task contains secret-like keys/values: {', '.join(secrets)}")

    return errors


def classify_change(
    rel_path: str,
    allowed_paths: List[str],
    forbidden_paths: List[str],
) -> str:
    """Classify a changed file as ``allowed``/``protected``/``out-of-scope``."""
    if matches_any(rel_path, forbidden_paths):
        return "protected"
    if not allowed_paths:
        return "out-of-scope"
    if matches_any(rel_path, allowed_paths):
        return "allowed"
    return "out-of-scope"


KNOWN_APPROVAL_ACTIONS = {"dependency-install", "db-migration", "start-service", "implement"}


def validate_repo_boundary(config: Dict[str, Any]) -> List[str]:
    """Validate every configured repository path stays inside the workspace.

    Returns a list of problems. Fail closed: if the boundary cannot be
    established, no run may proceed.
    """
    problems: List[str] = []
    workspace = config.get("workspace_root")
    if not workspace:
        problems.append("config is missing workspace_root")
        return problems

    def check(kind: str, value: Any) -> None:
        if not value:
            problems.append(f"config is missing {kind}")
            return
        text = str(value)
        resolved = os.path.normcase(os.path.realpath(text))
        ws = os.path.normcase(os.path.realpath(str(workspace)))
        try:
            inside = os.path.commonpath([ws, resolved]) == ws
        except ValueError:
            inside = False
        if not inside:
            problems.append(f"{kind} {text!r} resolves outside workspace_root {workspace!r}")

    check("repo_root", config.get("repo_root"))
    check("runs_dir", config.get("runs_dir"))
    check("tasks_dir", config.get("tasks_dir"))
    check("state_file", config.get("state_file"))
    return problems


def resolve_allowed_paths(repo_root: str, allowed_paths: List[str]) -> List[Path]:
    """Resolve allowed glob patterns to absolute paths and assert they stay in-repo."""
    resolved: List[Path] = []
    for pattern in allowed_paths:
        prefix = re.split(r"[*?\[]", pattern, maxsplit=1)[0].rstrip("/\\")
        candidate = Path(repo_root) / prefix if prefix else Path(repo_root)
        if not is_within(repo_root, candidate):
            raise ValueError(f"allowed path escapes repository: {pattern!r}")
        resolved.append(candidate)
    return resolved

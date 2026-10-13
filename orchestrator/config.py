"""Configuration loading for the Sentinel orchestrator.

The configuration is a JSON file (default ``orchestrator.config.json`` at the
workspace root). Paths may be absolute or relative to the config file's
directory. Missing keys are filled from ``DEFAULT_CONFIG`` so partial configs
remain valid.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict

DEFAULT_CONFIG: Dict[str, Any] = {
    "workspace_root": ".",
    "repo_root": "",
    "runs_dir": "runs",
    "tasks_dir": "tasks",
    "state_file": "state/state.json",
    "max_fix_attempts": 3,
    "queue_stop_on_blocked": True,
    "ignore_dirs": [
        ".git",
        "node_modules",
        ".next",
        "test-results",
        ".turbo",
        "dist",
        "build",
        "coverage",
        "__pycache__",
        ".venv",
        "venv",
        ".pytest_cache",
        ".mypy_cache",
    ],
    "ignore_globs": [
        "*.tsbuildinfo",
        "**/*.tsbuildinfo",
        "next-env.d.ts",
        "**/next-env.d.ts",
        ".next/**",
        "**/.next/**",
        "**/node_modules/**",
        "**/test-results/**",
        "**/playwright-report/**",
        "**/coverage/**",
        "**/blob-report/**",
        "**/*.log",
    ],
    "protected_paths": [
        ".env",
        ".env.*",
        "**/.env",
        "**/.env.*",
        "**/*.pem",
        "**/*.key",
        "**/*.p12",
        "secrets/**",
        "**/secrets/**",
        "deployments/**",
        "migrations/**",
    ],
    "opencode": {
        "command": "opencode",
        "model": "opencode/big-pickle",
        "timeout_seconds": 900,
        "agent": "",
        "extra_args": [],
        # Explicit config (agents/permissions) loaded via OPENCODE_CONFIG so the
        # controls apply to any target repo, not only the workspace.
        "agent_config": "",
    },
    # Role-based model assignment. Model IDs are configurable, never hard-coded
    # in the workflow. Verified against `opencode models` (see evidence doc).
    "roles": {
        "coder": {
            "model": "opencode/big-pickle",
            "agent": "orchestrator-coder",
            "fallback_models": ["opencode/ling-3.1-flash-free"],
        },
        "reviewer": {
            "model": "opencode/nemotron-3-ultra-free",
            "agent": "orchestrator-reviewer",
            "fallback_models": [],
        },
        # QA is deterministic and local; no model is involved.
        "qa": {"commands": []},
    },
    "review": {
        "enabled": True,
        "max_rounds": 3,
        "require_independent_model": True,
    },
    "provider_limits": {
        "transient_retries": 1,
        "backoff_seconds": 0.0,
    },
    # Extra allowlist regexes appended to policy's safe-command patterns.
    "safe_commands": [],
}


class ConfigError(Exception):
    """Raised when the configuration is missing or invalid."""


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _resolve(base_dir: Path, value: str) -> str:
    path = Path(value)
    if not path.is_absolute():
        path = base_dir / path
    return str(path.resolve())


def load_config(config_path: str | os.PathLike[str]) -> Dict[str, Any]:
    """Load, default-fill and normalize a config file.

    Raises:
        ConfigError: if the file is missing, unreadable or not a JSON object.
    """
    path = Path(config_path)
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:  # pragma: no cover - defensive
        raise ConfigError(f"cannot read config file {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a JSON object")

    cfg = _deep_merge(DEFAULT_CONFIG, raw)
    base_dir = path.resolve().parent

    cfg["config_path"] = str(path.resolve())
    cfg["workspace_root"] = _resolve(base_dir, str(cfg["workspace_root"]))
    if not cfg.get("repo_root"):
        raise ConfigError("config 'repo_root' is required")
    cfg["repo_root"] = _resolve(base_dir, str(cfg["repo_root"]))
    cfg["runs_dir"] = _resolve(base_dir, str(cfg["runs_dir"]))
    cfg["tasks_dir"] = _resolve(base_dir, str(cfg["tasks_dir"]))
    cfg["state_file"] = _resolve(base_dir, str(cfg["state_file"]))

    if not Path(cfg["repo_root"]).is_dir():
        raise ConfigError(f"repo_root is not a directory: {cfg['repo_root']}")

    try:
        cfg["max_fix_attempts"] = int(cfg["max_fix_attempts"])
    except (TypeError, ValueError) as exc:
        raise ConfigError("max_fix_attempts must be an integer") from exc
    if cfg["max_fix_attempts"] < 0:
        raise ConfigError("max_fix_attempts must be >= 0")

    if not isinstance(cfg["opencode"], dict):
        raise ConfigError("opencode section must be an object")
    oc = cfg["opencode"]
    if not isinstance(oc.get("extra_args"), list):
        raise ConfigError("opencode.extra_args must be a list")

    cfg["roles"] = _normalize_roles(cfg.get("roles"))
    cfg["review"] = _normalize_review(cfg.get("review"))
    cfg["provider_limits"] = _normalize_provider_limits(cfg.get("provider_limits"))
    if not isinstance(cfg.get("safe_commands"), list):
        raise ConfigError("safe_commands must be a list")

    if cfg["review"].get("require_independent_model") and (
        cfg["roles"]["coder"]["model"] == cfg["roles"]["reviewer"]["model"]
    ):
        raise ConfigError(
            "roles.reviewer.model must differ from roles.coder.model to keep "
            "review independent"
        )
    if not oc.get("agent_config"):
        oc["agent_config"] = str(module_dir() / "opencode.agents.json")

    return cfg


def _normalize_roles(roles: Any) -> Dict[str, Any]:
    if not isinstance(roles, dict):
        raise ConfigError("roles must be an object")
    defaults = DEFAULT_CONFIG["roles"]
    result: Dict[str, Any] = {}
    for name in ("coder", "reviewer", "qa"):
        raw = roles.get(name)
        base = dict(defaults.get(name) or {})
        if raw is not None:
            if not isinstance(raw, dict):
                raise ConfigError(f"roles.{name} must be an object")
            base.update(raw)
        if name in ("coder", "reviewer"):
            if not isinstance(base.get("model"), str) or not base["model"].strip():
                raise ConfigError(f"roles.{name}.model is required")
            if not isinstance(base.get("fallback_models"), list):
                base["fallback_models"] = []
            base["fallback_models"] = [
                m for m in base["fallback_models"] if isinstance(m, str) and m.strip()
            ]
        else:
            if not isinstance(base.get("commands"), list):
                base["commands"] = []
        result[name] = base
    return result


def _normalize_review(review: Any) -> Dict[str, Any]:
    if review is None:
        review = {}
    if not isinstance(review, dict):
        raise ConfigError("review must be an object")
    max_rounds = int(review.get("max_rounds", DEFAULT_CONFIG["review"]["max_rounds"]))
    if max_rounds < 0:
        raise ConfigError("review.max_rounds must be >= 0")
    return {
        "enabled": bool(review.get("enabled", DEFAULT_CONFIG["review"]["enabled"])),
        "max_rounds": max_rounds,
        "require_independent_model": bool(
            review.get(
                "require_independent_model",
                DEFAULT_CONFIG["review"]["require_independent_model"],
            )
        ),
    }


def _normalize_provider_limits(limits: Any) -> Dict[str, Any]:
    if limits is None:
        limits = {}
    if not isinstance(limits, dict):
        raise ConfigError("provider_limits must be an object")
    retries = int(limits.get("transient_retries", DEFAULT_CONFIG["provider_limits"]["transient_retries"]))
    if retries < 0:
        raise ConfigError("provider_limits.transient_retries must be >= 0")
    backoff = float(limits.get("backoff_seconds", DEFAULT_CONFIG["provider_limits"]["backoff_seconds"]))
    if backoff < 0:
        raise ConfigError("provider_limits.backoff_seconds must be >= 0")
    return {"transient_retries": retries, "backoff_seconds": backoff}


def role_models(config: Dict[str, Any], role: str) -> List[str]:
    """Ordered model list for a role: primary, then configured fallbacks."""
    role_cfg = (config.get("roles") or {}).get(role) or {}
    primary = role_cfg.get("model")
    result: List[str] = []
    if primary:
        result.append(str(primary))
    for model in role_cfg.get("fallback_models") or []:
        if model and model not in result:
            result.append(str(model))
    return result


def role_agent(config: Dict[str, Any], role: str) -> str:
    role_cfg = (config.get("roles") or {}).get(role) or {}
    return str(role_cfg.get("agent") or "")


def qa_commands(config: Dict[str, Any]) -> List[str]:
    qa = (config.get("roles") or {}).get("qa") or {}
    return list(qa.get("commands") or [])


def module_dir() -> Path:
    """Directory containing this package."""
    return Path(__file__).resolve().parent

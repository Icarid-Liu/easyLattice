from __future__ import annotations

import ast
import importlib.metadata
import importlib.util
import json
import os
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class EstimatorConfig:
    sage_binary: str = "sage"
    lattice_estimator_path: str | None = None
    default_timeout_seconds: int = 16
    per_attack_timeout_seconds: int = 12
    remote_url: str | None = None
    remote_timeout_seconds: int = 240
    remote_poll_interval_seconds: float = 2.0


@dataclass(frozen=True)
class LLMConfig:
    enabled: bool = False
    provider: str = "openai-compatible"
    base_url: str = "http://localhost:11434/v1"
    model: str = "local-model"
    api_key_env: str = "EASYLATTICE_LLM_API_KEY"
    auth_header: str = "Authorization"
    auth_prefix: str = "Bearer "
    timeout_seconds: int = 30


@dataclass(frozen=True)
class ScriptsConfig:
    decrypt_error: list[str] = field(default_factory=list)
    signature_smoothing: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AppConfig:
    estimator: EstimatorConfig = field(default_factory=EstimatorConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    scripts: ScriptsConfig = field(default_factory=ScriptsConfig)
    source: str = "defaults"


def load_config() -> AppConfig:
    raw: dict[str, Any] = {}
    source = "defaults"
    config_path = env_value("EASYLATTICE_CONFIG")
    if config_path:
        raw = read_json(Path(config_path))
        source = config_path
    else:
        local = ROOT / "config.local.json"
        if local.exists():
            raw = read_json(local)
            source = str(local)

    estimator_raw = raw.get("estimator", {})
    llm_raw = raw.get("llm", raw.get("model", {}))
    scripts_raw = raw.get("scripts", {})

    estimator = EstimatorConfig(
        sage_binary=os.environ.get("SAGE_BINARY", estimator_raw.get("sage_binary", "sage")),
        lattice_estimator_path=os.environ.get(
            "LATTICE_ESTIMATOR_PATH",
            estimator_raw.get("lattice_estimator_path"),
        ),
        default_timeout_seconds=int(
            env_value(
                "EASYLATTICE_ESTIMATOR_TIMEOUT",
                default=estimator_raw.get("default_timeout_seconds", 16),
            )
        ),
        per_attack_timeout_seconds=int(
            env_value(
                "EASYLATTICE_ESTIMATOR_PER_ATTACK_TIMEOUT",
                default=estimator_raw.get("per_attack_timeout_seconds", 12),
            )
        ),
        remote_url=env_value(
            "EASYLATTICE_ESTIMATOR_REMOTE_URL",
            default=estimator_raw.get("remote_url"),
        ),
        remote_timeout_seconds=max(
            1,
            min(
                300,
                int(
                    env_value(
                        "EASYLATTICE_ESTIMATOR_REMOTE_TIMEOUT",
                        default=estimator_raw.get("remote_timeout_seconds", 240),
                    )
                ),
            ),
        ),
        remote_poll_interval_seconds=max(
            0.5,
            min(
                15.0,
                float(
                    env_value(
                        "EASYLATTICE_ESTIMATOR_REMOTE_POLL_INTERVAL",
                        default=estimator_raw.get("remote_poll_interval_seconds", 2.0),
                    )
                ),
            ),
        ),
    )
    llm = LLMConfig(
        enabled=parse_bool(
            env_value(
                "EASYLATTICE_LLM_ENABLED",
                default=llm_raw.get("enabled", False),
            )
        ),
        provider=env_value(
            "EASYLATTICE_LLM_PROVIDER",
            "EASYLATTICE_MODEL_PROVIDER",
            default=llm_raw.get("provider", "openai-compatible"),
        ),
        base_url=env_value(
            "EASYLATTICE_LLM_BASE_URL",
            "EASYLATTICE_MODEL_BASE_URL",
            default=llm_raw.get("base_url", "http://localhost:11434/v1"),
        ),
        model=env_value(
            "EASYLATTICE_LLM_MODEL",
            "EASYLATTICE_MODEL",
            default=llm_raw.get("model", "local-model"),
        ),
        api_key_env=env_value(
            "EASYLATTICE_LLM_API_KEY_ENV",
            "EASYLATTICE_MODEL_API_KEY_ENV",
            default=llm_raw.get("api_key_env", "EASYLATTICE_LLM_API_KEY"),
        ),
        auth_header=env_value(
            "EASYLATTICE_LLM_AUTH_HEADER",
            default=llm_raw.get("auth_header", "Authorization"),
        ),
        auth_prefix=env_value(
            "EASYLATTICE_LLM_AUTH_PREFIX",
            default=llm_raw.get("auth_prefix", "Bearer "),
        ),
        timeout_seconds=int(
            env_value(
                "EASYLATTICE_LLM_TIMEOUT",
                default=llm_raw.get("timeout_seconds", 30),
            )
        ),
    )
    scripts = ScriptsConfig(
        decrypt_error=list(scripts_raw.get("decrypt_error", [])),
        signature_smoothing=list(scripts_raw.get("signature_smoothing", [])),
    )
    return AppConfig(estimator=estimator, llm=llm, scripts=scripts, source=source)


def env_value(*names: str, default: Any = None) -> Any:
    for name in names:
        value = os.environ.get(name)
        if value is not None:
            return value
    return default


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def read_json(path: Path) -> dict[str, Any]:
    with path.expanduser().open("r", encoding="utf-8") as handle:
        return json.load(handle)


def public_config(config: AppConfig | None = None) -> dict[str, Any]:
    config = config or load_config()
    data = asdict(config)
    data["estimator"]["version"] = estimator_version(config.estimator)
    data["estimator"]["remote_configured"] = bool(data["estimator"].get("remote_url"))
    api_key_env = data["llm"]["api_key_env"]
    api_key_present = bool(os.environ.get(api_key_env))
    auth_header = data["llm"].get("auth_header", "")
    data["llm"]["api_key_present"] = api_key_present
    data["llm"]["configured"] = bool(data["llm"]["enabled"] and (api_key_present or not auth_header))
    data["llm"]["mode"] = "optional-enhancement" if data["llm"]["enabled"] else "disabled"
    data["llm"].pop("api_key_env", None)
    data["llm"].pop("auth_prefix", None)
    return data


def estimator_version(estimator: EstimatorConfig) -> str | None:
    root = estimator_source_root(estimator)
    if root:
        return read_git_version(root) or read_static_estimator_version(root)
    return read_installed_estimator_version()


def estimator_source_root(estimator: EstimatorConfig) -> Path | None:
    if estimator.lattice_estimator_path:
        return normalize_estimator_root(Path(estimator.lattice_estimator_path).expanduser())

    spec = importlib.util.find_spec("estimator")
    if not spec or not spec.origin:
        return None

    try:
        origin = Path(spec.origin).resolve()
    except OSError:
        return None

    if origin.name == "__init__.py" and origin.parent.name == "estimator":
        return origin.parent.parent
    return normalize_estimator_root(origin.parent)


def normalize_estimator_root(path: Path) -> Path:
    if path.name == "estimator":
        return path.parent
    return path


def read_git_version(root: Path) -> str | None:
    if not root.exists():
        return None

    for command in (
        ["git", "-C", str(root), "describe", "--tags", "--always", "--dirty"],
        ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
    ):
        try:
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                timeout=2,
                check=False,
            )
        except Exception:
            continue
        if completed.returncode == 0:
            value = completed.stdout.strip()
            if value:
                return value
    return None


def read_static_estimator_version(root: Path) -> str | None:
    init_path = root / "estimator" / "__init__.py"
    if not init_path.exists():
        return None

    try:
        module = ast.parse(init_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets):
            continue
        try:
            value = ast.literal_eval(node.value)
        except Exception:
            return None
        return str(value) if value else None
    return None


def read_installed_estimator_version() -> str | None:
    try:
        return importlib.metadata.version("lattice-estimator")
    except importlib.metadata.PackageNotFoundError:
        return None

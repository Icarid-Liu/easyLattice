from __future__ import annotations

import json
import os
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


@dataclass(frozen=True)
class ModelConfig:
    provider: str = "openai-compatible"
    base_url: str = "http://localhost:11434/v1"
    model: str = "local-model"
    api_key_env: str = "AILATTICE_API_KEY"


@dataclass(frozen=True)
class ScriptsConfig:
    decrypt_error: list[str] = field(default_factory=list)
    signature_smoothing: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AppConfig:
    estimator: EstimatorConfig = field(default_factory=EstimatorConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    scripts: ScriptsConfig = field(default_factory=ScriptsConfig)
    source: str = "defaults"


def load_config() -> AppConfig:
    raw: dict[str, Any] = {}
    source = "defaults"
    config_path = os.environ.get("AILATTICE_CONFIG")
    if config_path:
        raw = read_json(Path(config_path))
        source = config_path
    else:
        local = ROOT / "config.local.json"
        if local.exists():
            raw = read_json(local)
            source = str(local)

    estimator_raw = raw.get("estimator", {})
    model_raw = raw.get("model", {})
    scripts_raw = raw.get("scripts", {})

    estimator = EstimatorConfig(
        sage_binary=os.environ.get("SAGE_BINARY", estimator_raw.get("sage_binary", "sage")),
        lattice_estimator_path=os.environ.get(
            "LATTICE_ESTIMATOR_PATH",
            estimator_raw.get("lattice_estimator_path"),
        ),
        default_timeout_seconds=int(
            os.environ.get(
                "AILATTICE_ESTIMATOR_TIMEOUT",
                estimator_raw.get("default_timeout_seconds", 16),
            )
        ),
        per_attack_timeout_seconds=int(
            os.environ.get(
                "AILATTICE_ESTIMATOR_PER_ATTACK_TIMEOUT",
                estimator_raw.get("per_attack_timeout_seconds", 12),
            )
        ),
    )
    model = ModelConfig(
        provider=os.environ.get(
            "AILATTICE_MODEL_PROVIDER",
            model_raw.get("provider", "openai-compatible"),
        ),
        base_url=os.environ.get(
            "AILATTICE_MODEL_BASE_URL",
            model_raw.get("base_url", "http://localhost:11434/v1"),
        ),
        model=os.environ.get("AILATTICE_MODEL", model_raw.get("model", "local-model")),
        api_key_env=os.environ.get(
            "AILATTICE_MODEL_API_KEY_ENV",
            model_raw.get("api_key_env", "AILATTICE_API_KEY"),
        ),
    )
    scripts = ScriptsConfig(
        decrypt_error=list(scripts_raw.get("decrypt_error", [])),
        signature_smoothing=list(scripts_raw.get("signature_smoothing", [])),
    )
    return AppConfig(estimator=estimator, model=model, scripts=scripts, source=source)


def read_json(path: Path) -> dict[str, Any]:
    with path.expanduser().open("r", encoding="utf-8") as handle:
        return json.load(handle)


def public_config(config: AppConfig | None = None) -> dict[str, Any]:
    config = config or load_config()
    data = asdict(config)
    api_key_env = data["model"]["api_key_env"]
    data["model"]["api_key_present"] = bool(os.environ.get(api_key_env))
    data["model"].pop("api_key_env", None)
    return data

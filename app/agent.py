from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .config import AppConfig, load_config
from .llm_provider import LLMConfigurationError, OpenAICompatibleLLM
from .ntru_search import recommend_ntru
from .parameter_search import recommend_rlwe


@dataclass(frozen=True)
class AgentMode:
    name: str
    llm_used: bool
    description: str


DETERMINISTIC_MODE = AgentMode(
    name="deterministic",
    llm_used=False,
    description="Fixed RLWE search policy; no model call and no API key required.",
)

LLM_ASSISTED_MODE = AgentMode(
    name="llm-assisted",
    llm_used=True,
    description="User-owned LLM interprets free-form intent into deterministic search constraints.",
)


def recommend_with_agent(raw: dict[str, Any] | None = None, config: AppConfig | None = None) -> dict[str, Any]:
    payload = raw or {}
    config = config or load_config()
    deterministic_request = extract_request_payload(payload)
    intent = str(payload.get("intent", payload.get("prompt", ""))).strip()
    use_llm = bool(payload.get("use_llm", payload.get("useLLM", payload.get("useLlm", False))))

    if not use_llm:
        result = run_deterministic_search(deterministic_request, config=config)
        result["agent"] = {
            **asdict(DETERMINISTIC_MODE),
            "intent_present": bool(intent),
            "notes": [
                "Default mode is deterministic and never calls an LLM.",
                "Free-form intent is ignored unless useLLM=true and local LLM config is enabled.",
            ],
        }
        return result

    if not intent:
        raise ValueError("intent is required when useLLM=true.")
    if not config.llm.enabled:
        raise LLMConfigurationError("LLM enhancement is disabled. Set llm.enabled=true in config.local.json.")

    interpretation = OpenAICompatibleLLM(config.llm).interpret_request(intent, deterministic_request)
    merged_request = {**deterministic_request, **interpretation.overrides}
    result = run_deterministic_search(merged_request, config=config)
    result["agent"] = {
        **asdict(LLM_ASSISTED_MODE),
        "provider": config.llm.provider,
        "model": config.llm.model,
        "overrides": interpretation.overrides,
        "explanation": interpretation.explanation,
        "notes": [
            "The LLM only converts intent into constraints; final parameters still come from deterministic search.",
            "No maintainer token is used. Authentication comes from the user's local environment.",
        ],
    }
    return result


def run_deterministic_search(payload: dict[str, Any], config: AppConfig | None = None) -> dict[str, Any]:
    problem = str(payload.get("problem", "rlwe")).lower()
    if problem == "ntru":
        return recommend_ntru(payload, config=config)
    if problem == "rlwe":
        return recommend_rlwe(payload, config=config)
    raise ValueError("problem must be one of rlwe, ntru.")


def extract_request_payload(payload: dict[str, Any]) -> dict[str, Any]:
    request = payload.get("request")
    if isinstance(request, dict):
        return dict(request)
    return {
        key: value
        for key, value in payload.items()
        if key
        not in {
            "intent",
            "prompt",
            "use_llm",
            "useLLM",
            "useLlm",
            "agent",
        }
    }

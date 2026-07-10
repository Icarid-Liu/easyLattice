from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import LLMConfig


ALLOWED_OVERRIDE_KEYS = {
    "problem",
    "targetSecurity",
    "target_security",
    "ringFamily",
    "ring_family",
    "securityModel",
    "security_model",
    "redCostModel",
    "red_cost_model",
    "nttScalePower",
    "ntt_scale_power",
    "minQBits",
    "min_q_bits",
    "maxQBits",
    "max_q_bits",
    "minN",
    "min_n",
    "maxN",
    "max_n",
    "distribution",
    "secretDistribution",
    "secret_distribution",
    "errorDistribution",
    "error_distribution",
    "compressionP",
    "p",
    "useEstimator",
    "use_estimator",
    "validationCount",
    "validation_count",
    "validationAttempts",
    "validation_attempts",
}


class LLMConfigurationError(ValueError):
    pass


class LLMResponseError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMInterpretation:
    overrides: dict[str, Any]
    explanation: str
    raw_text: str


class OpenAICompatibleLLM:
    def __init__(self, config: LLMConfig):
        self.config = config

    def interpret_request(self, intent: str, current_request: dict[str, Any]) -> LLMInterpretation:
        if not self.config.enabled:
            raise LLMConfigurationError("LLM enhancement is disabled. Set llm.enabled=true in config.local.json.")

        api_key = os.environ.get(self.config.api_key_env, "")
        if self.config.auth_header and not api_key:
            raise LLMConfigurationError(
                f"LLM authentication is not configured. Set environment variable {self.config.api_key_env}."
            )

        payload = {
            "model": self.config.model,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You convert lattice-crypto parameter-search intent into a small JSON object. "
                        "Return only JSON. Use only these keys when needed: targetSecurity, ringFamily, "
                        "securityModel, redCostModel, nttScalePower, minQBits, maxQBits, minN, maxN, "
                        "distribution, secretDistribution, errorDistribution, compressionP, useEstimator, "
                        "validationCount, validationAttempts. "
                        "Valid ringFamily values are power2 and ternary. Valid securityModel values are "
                        "classical and quantum. Valid redCostModel values are matzov and adps16. "
                        "Use secretDistribution and errorDistribution when the user distinguishes Xs and Xe. "
                        "For LWR, RLWR, and MLWR variants, errorDistribution is a compression modulus p, "
                        "not a uniform distribution. "
                        "Do not invent security claims."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "intent": intent,
                            "current_request": current_request,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        }
        raw_text = self._chat(payload)
        parsed = parse_json_object(raw_text)
        overrides = sanitize_overrides(parsed.get("overrides", parsed))
        explanation = str(parsed.get("explanation", "")).strip()
        return LLMInterpretation(overrides=overrides, explanation=explanation, raw_text=raw_text)

    def _chat(self, payload: dict[str, Any]) -> str:
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        api_key = os.environ.get(self.config.api_key_env, "")
        if self.config.auth_header and api_key:
            request.add_header(self.config.auth_header, f"{self.config.auth_prefix}{api_key}")

        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise LLMResponseError(f"LLM provider returned HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise LLMResponseError(f"LLM provider request failed: {exc.reason}") from exc

        try:
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMResponseError("LLM provider returned an unsupported chat-completions response.") from exc


def sanitize_overrides(raw: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in raw.items() if key in ALLOWED_OVERRIDE_KEYS}


def parse_json_object(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise LLMResponseError("LLM response did not contain a JSON object.")
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise LLMResponseError("LLM response JSON must be an object.")
    return value

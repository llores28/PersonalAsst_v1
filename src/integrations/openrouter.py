from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml
from tenacity import retry, stop_after_attempt, wait_exponential

from src.models.cost_tracker import check_cost_cap, estimate_cost_from_model, track_cost
from src.models.provider_resolution import ProviderResolver
from src.settings import settings

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "openrouter_capabilities.yaml"


@dataclass(frozen=True)
class OpenRouterImageResult:
    prompt: str
    revised_prompt: str
    model: str
    mime_type: str
    data_base64: str

    @property
    def data_bytes(self) -> bytes:
        return base64.b64decode(self.data_base64)


@dataclass(frozen=True)
class OpenRouterImageAnalysisResult:
    prompt: str
    analysis: str
    model: str


def _load_capability_config() -> dict[str, Any]:
    if not _CONFIG_PATH.exists():
        return {}
    with _CONFIG_PATH.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _select_model_for_quality(quality: str, capability_name: str = "generate_image") -> str:
    config = _load_capability_config()
    capability = (config.get("capabilities") or {}).get(capability_name) or {}
    preferred_models = capability.get("preferred_models") or []
    if not preferred_models:
        raise RuntimeError(f"No OpenRouter models are configured for capability '{capability_name}'.")

    quality_tiers = config.get("quality_tiers") or {}
    prefer_index = int((quality_tiers.get(quality) or {}).get("prefer_index", 0))
    return preferred_models[prefer_index]


async def _check_openrouter_preconditions(user_id: int) -> None:
    if not settings.openrouter_image_enabled:
        raise RuntimeError("OpenRouter image generation is disabled.")
    if not settings.openrouter_api_key:
        raise RuntimeError("OpenRouter is not configured.")

    capped, current_cost, cap_limit = await check_cost_cap(user_id, "openrouter")
    if capped:
        raise RuntimeError(
            f"OpenRouter daily cost cap reached (${current_cost:.2f} / ${cap_limit:.2f})."
        )


async def _track_openrouter_usage(
    *,
    user_id: int,
    model: str,
    payload: dict[str, Any],
) -> None:
    usage = payload.get("usage") or {}
    input_tokens = int(usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("completion_tokens") or 0)
    estimated_cost, matched_key = estimate_cost_from_model(model, input_tokens, output_tokens)
    if matched_key is None:
        logger.warning(
            "OpenRouter cost tracking: model '%s' not in pricing table — using default rate. "
            "Add it to OPENAI_MODEL_PRICING in src/models/cost_tracker.py.",
            model,
        )
    await track_cost(
        user_id=user_id,
        provider="openrouter",
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=estimated_cost,
    )


def _openrouter_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/llores28/PersonalAsst",
        "X-OpenRouter-Title": "Atlas Personal Assistant",
    }


def _infer_image_config(prompt: str, quality: str) -> dict[str, str]:
    lowered = " ".join((prompt or "").strip().lower().split())
    image_config: dict[str, str] = {}

    if any(term in lowered for term in ("landscape", "wide", "desktop background", "wallpaper wide", "16:9")):
        image_config["aspect_ratio"] = "16:9"
    elif any(term in lowered for term in ("portrait", "phone wallpaper", "vertical", "9:16")):
        image_config["aspect_ratio"] = "9:16"
    elif "square" in lowered or "1:1" in lowered:
        image_config["aspect_ratio"] = "1:1"

    if quality == "best":
        image_config["image_size"] = "2K"
    elif quality == "fast":
        image_config["image_size"] = "1K"

    return image_config


async def list_image_models() -> list[dict[str, Any]]:
    resolver = ProviderResolver()
    config = resolver.resolve("openrouter")

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(
            f"{config.base_url}/models",
            params={"output_modalities": "image"},
            headers=_openrouter_headers(config.api_key or ""),
        )
        response.raise_for_status()

    payload = response.json()
    return payload.get("data") or []


async def list_models_by_modality(modality: str) -> list[dict[str, Any]]:
    """Return OpenRouter models that produce the given output modality.

    Args:
        modality: One of "image", "video", "audio". Used as the
                  output_modalities filter on the /models endpoint.

    Returns:
        List of model dicts, each containing at minimum:
          id, name, description, pricing (prompt/completion cost per token or per second),
          context_length, top_provider.
        Sorted cheapest-first by pricing.prompt.
    """
    resolver = ProviderResolver()
    config = resolver.resolve("openrouter")

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(
            f"{config.base_url}/models",
            params={"output_modalities": modality},
            headers=_openrouter_headers(config.api_key or ""),
        )
        response.raise_for_status()

    models: list[dict[str, Any]] = response.json().get("data") or []

    def _price(m: dict) -> float:
        pricing = m.get("pricing") or {}
        try:
            return float(pricing.get("prompt") or pricing.get("image") or pricing.get("completion") or 999)
        except (TypeError, ValueError):
            return 999.0

    return sorted(models, key=_price)


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=4), reraise=True)
async def generate_image(
    *,
    user_id: int,
    prompt: str,
    quality: str = "balanced",
) -> OpenRouterImageResult:
    await _check_openrouter_preconditions(user_id)

    resolver = ProviderResolver()
    config = resolver.resolve("openrouter")
    model = _select_model_for_quality(quality, "generate_image")
    capability_config = (_load_capability_config().get("capabilities") or {}).get("generate_image") or {}
    timeout_seconds = int(capability_config.get("timeout_seconds", 60))

    request_body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
        "modalities": ["image", "text"],
    }
    image_config = _infer_image_config(prompt, quality)
    if image_config:
        request_body["image_config"] = image_config

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.post(
            f"{config.base_url}/chat/completions",
            headers=_openrouter_headers(config.api_key or ""),
            json=request_body,
        )
        response.raise_for_status()

    payload = response.json()
    choice = ((payload.get("choices") or [{}])[0])
    message = choice.get("message") or {}
    images = message.get("images") or []
    if not images:
        logger.error("OpenRouter image response missing images field: %s", payload)
        raise RuntimeError("OpenRouter did not return an image.")

    image_obj = images[0]
    image_url = ((image_obj.get("image_url") or {}).get("url")) or ((image_obj.get("imageUrl") or {}).get("url"))
    if not image_url or not image_url.startswith("data:"):
        raise RuntimeError("OpenRouter returned an unsupported image payload.")

    header, encoded = image_url.split(",", 1)
    mime_type = header.split(";", 1)[0].split(":", 1)[1]
    revised_prompt = message.get("content") or ""

    try:
        await _track_openrouter_usage(user_id=user_id, model=model, payload=payload)
    except Exception as exc:
        logger.warning("OpenRouter image cost tracking failed: %s", exc)

    return OpenRouterImageResult(
        prompt=prompt,
        revised_prompt=revised_prompt,
        model=model,
        mime_type=mime_type,
        data_base64=encoded,
    )


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=4), reraise=True)
async def analyze_image(
    *,
    user_id: int,
    prompt: str,
    image_bytes: bytes,
    mime_type: str,
    quality: str = "balanced",
) -> OpenRouterImageAnalysisResult:
    await _check_openrouter_preconditions(user_id)

    resolver = ProviderResolver()
    config = resolver.resolve("openrouter")
    model = _select_model_for_quality(quality, "analyze_image")
    capability_config = (_load_capability_config().get("capabilities") or {}).get("analyze_image") or {}
    timeout_seconds = int(capability_config.get("timeout_seconds", 45))
    data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('utf-8')}"

    request_body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
    }

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.post(
            f"{config.base_url}/chat/completions",
            headers=_openrouter_headers(config.api_key or ""),
            json=request_body,
        )
        response.raise_for_status()

    payload = response.json()
    choice = ((payload.get("choices") or [{}])[0])
    message = choice.get("message") or {}
    analysis = message.get("content") or ""
    if not analysis:
        raise RuntimeError("OpenRouter did not return an image analysis response.")

    try:
        await _track_openrouter_usage(user_id=user_id, model=model, payload=payload)
    except Exception as exc:
        logger.warning("OpenRouter image analysis cost tracking failed: %s", exc)

    return OpenRouterImageAnalysisResult(prompt=prompt, analysis=analysis, model=model)


def serialize_image_result(result: OpenRouterImageResult) -> str:
    return json.dumps(
        {
            "kind": "openrouter_image",
            "prompt": result.prompt,
            "revised_prompt": result.revised_prompt,
            "model": result.model,
            "mime_type": result.mime_type,
            "data_base64": result.data_base64,
        }
    )

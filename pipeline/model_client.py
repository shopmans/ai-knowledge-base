"""Unified LLM client supporting DeepSeek, Qwen, and Zhipu providers.

Uses httpx to call OpenAI-compatible APIs directly. Configurable via
environment variables: LLM_PROVIDER, and provider-specific API keys.

Usage::

    from pipeline.model_client import quick_chat

    reply = quick_chat("Explain transformers in one sentence.")
    print(reply.content)
"""

from __future__ import annotations

import logging
import math
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# .env auto-loading
# ---------------------------------------------------------------------------

def _load_dotenv() -> None:
    """Load environment variables from a ``.env`` file.

    Searches upward from this module's directory until a ``.env`` file is
    found.  Prefers the ``python-dotenv`` library when available; falls
    back to a minimal hand-rolled parser so the module never hard-depends
    on an extra package.

    Existing environment variables are **not** overwritten (mirrors the
    ``override=False`` behaviour of python-dotenv).
    """
    this_dir = Path(__file__).resolve().parent
    for candidate in [this_dir, *this_dir.parents]:
        env_path = candidate / ".env"
        if env_path.is_file():
            break
    else:
        return

    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
        logger.debug("Loaded .env via python-dotenv: %s", env_path)
        return
    except ImportError:
        pass

    pairs: dict[str, str] = {}
    with env_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            pairs[key] = value

    for k, v in pairs.items():
        os.environ.setdefault(k, v)

    logger.debug("Loaded .env (fallback parser): %s — %d vars", env_path, len(pairs))


_load_dotenv()

# ---------------------------------------------------------------------------
# Provider configuration registry
# ---------------------------------------------------------------------------

_PROVIDER_CONFIGS: dict[str, dict[str, str]] = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
        "default_model": "deepseek-chat",
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "QWEN_API_KEY",
        "default_model": "qwen-plus",
    },
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key_env": "ZHIPU_API_KEY",
        "default_model": "glm-4",
    },
}

# ---------------------------------------------------------------------------
# Pricing per 1M tokens (USD) — approximate public list prices
# ---------------------------------------------------------------------------

_PRICING: dict[str, dict[str, float]] = {
    "deepseek": {"prompt": 0.14, "completion": 0.28},
    "qwen": {"prompt": 0.40, "completion": 1.20},
    "zhipu": {"prompt": 0.10, "completion": 0.10},
}

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Usage:
    """Token usage statistics returned by the LLM provider.

    Attributes:
        prompt_tokens: Number of tokens in the prompt.
        completion_tokens: Number of tokens in the completion.
        total_tokens: Total tokens (prompt + completion).
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class LLMResponse:
    """Standardised response from any LLM provider.

    Attributes:
        content: The generated text.
        usage: Token usage statistics.
        model: The model name that produced the response.
        provider: The provider identifier (e.g. ``"deepseek"``).
        raw: The raw JSON response payload for debugging.
    """

    content: str = ""
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    provider: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Abstract provider & OpenAI-compatible implementation
# ---------------------------------------------------------------------------


class LLMProvider(ABC):
    """Abstract base class that every provider must implement."""

    @abstractmethod
    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        timeout: float = 60.0,
    ) -> LLMResponse:
        """Send a chat completion request.

        Args:
            messages: A list of message dicts with ``role`` and ``content``.
            model: Model identifier. ``None`` uses the provider default.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens in the completion.
            timeout: HTTP request timeout in seconds.

        Returns:
            An :class:`LLMResponse` instance.
        """


class OpenAICompatibleProvider(LLMProvider):
    """Provider that speaks the OpenAI Chat Completions API.

    Args:
        provider_name: Key in :data:`_PROVIDER_CONFIGS`.
        api_key: API key. If ``None``, read from the environment variable
            declared in the provider config.
        base_url: Override the default base URL.
        default_model: Override the default model name.
    """

    def __init__(
        self,
        provider_name: str,
        api_key: str | None = None,
        base_url: str | None = None,
        default_model: str | None = None,
    ) -> None:
        config = _PROVIDER_CONFIGS.get(provider_name)
        if config is None:
            raise ValueError(
                f"Unknown provider '{provider_name}'. "
                f"Available: {list(_PROVIDER_CONFIGS)}"
            )

        self.provider_name = provider_name
        self.base_url = (
            base_url or os.environ.get("LLM_BASE_URL") or config["base_url"]
        ).rstrip("/")
        self.api_key = api_key or os.environ.get(config["api_key_env"], "")
        self.default_model = (
            default_model or os.environ.get("LLM_MODEL") or config["default_model"]
        )

        if not self.api_key:
            logger.warning(
                "API key for %s is empty. Set %s.",
                provider_name,
                config["api_key_env"],
            )

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        timeout: float = 60.0,
    ) -> LLMResponse:
        model = model or self.default_model
        url = f"{self.base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        logger.debug(
            "Sending request to %s model=%s messages=%d",
            self.provider_name,
            model,
            len(messages),
        )

        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            body = resp.json()

        choice = body.get("choices", [{}])[0]
        message = choice.get("message", {})
        content = message.get("content", "")

        usage_raw = body.get("usage", {})
        usage = Usage(
            prompt_tokens=usage_raw.get("prompt_tokens", 0),
            completion_tokens=usage_raw.get("completion_tokens", 0),
            total_tokens=usage_raw.get("total_tokens", 0),
        )

        logger.debug(
            "Received response: tokens=%d/%d",
            usage.prompt_tokens,
            usage.completion_tokens,
        )

        return LLMResponse(
            content=content,
            usage=usage,
            model=body.get("model", model),
            provider=self.provider_name,
            raw=body,
        )


# ---------------------------------------------------------------------------
# Factory helper
# ---------------------------------------------------------------------------


def create_provider(
    provider_name: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    default_model: str | None = None,
) -> OpenAICompatibleProvider:
    """Create a provider from a name (or ``LLM_PROVIDER`` env var).

    Args:
        provider_name: Provider identifier. Falls back to ``LLM_PROVIDER``
            then ``"deepseek"``.
        api_key: Optional API key override.
        base_url: Optional base URL override.
        default_model: Optional default model override.

    Returns:
        An :class:`OpenAICompatibleProvider` ready to use.
    """
    name = provider_name or os.environ.get("LLM_PROVIDER", "zhipu")
    return OpenAICompatibleProvider(
        provider_name=name,
        api_key=api_key,
        base_url=base_url,
        default_model=default_model,
    )


# ---------------------------------------------------------------------------
# Retry wrapper
# ---------------------------------------------------------------------------


def chat_with_retry(
    messages: list[dict[str, str]],
    *,
    provider: LLMProvider | None = None,
    max_retries: int = 3,
    base_delay: float = 1.0,
    **chat_kwargs: Any,
) -> LLMResponse:
    """Call ``provider.chat`` with exponential-backoff retry.

    Args:
        messages: Chat messages.
        provider: Provider instance. ``None`` creates one via
            :func:`create_provider`.
        max_retries: Maximum number of attempts.
        base_delay: Initial delay in seconds (doubles each retry).
        **chat_kwargs: Forwarded to ``provider.chat``.

    Returns:
        The :class:`LLMResponse` from the last successful attempt.

    Raises:
        httpx.HTTPStatusError: If all retries are exhausted.
    """
    if provider is None:
        provider = create_provider()

    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return provider.chat(messages, **chat_kwargs)
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            last_exc = exc
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "Attempt %d/%d failed: %s — retrying in %.1fs",
                attempt,
                max_retries,
                exc,
                delay,
            )
            if attempt < max_retries:
                time.sleep(delay)

    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Token estimation & cost helpers
# ---------------------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """Estimate the number of tokens in *text*.

    Uses a simple heuristic: ~1.3 tokens per English word, ~1.5 tokens per
    Chinese character, plus a small overhead.

    Args:
        text: Input text.

    Returns:
        Estimated token count.
    """
    char_count = len(text)
    chinese_chars = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    non_chinese_chars = char_count - chinese_chars
    word_estimate = non_chinese_chars / 4
    tokens = math.ceil(word_estimate * 1.3 + chinese_chars * 1.5 + 3)
    return max(tokens, 1)


def calculate_cost(
    usage: Usage,
    provider: str = "deepseek",
) -> float:
    """Calculate the approximate cost in USD for a request.

    Args:
        usage: Token usage from an :class:`LLMResponse`.
        provider: Provider name used to look up pricing.

    Returns:
        Estimated cost in USD.
    """
    pricing = _PRICING.get(provider, _PRICING["deepseek"])
    prompt_cost = usage.prompt_tokens / 1_000_000 * pricing["prompt"]
    completion_cost = usage.completion_tokens / 1_000_000 * pricing["completion"]
    return round(prompt_cost + completion_cost, 8)


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------


def quick_chat(
    prompt: str,
    *,
    system: str = "You are a helpful assistant.",
    provider_name: str | None = None,
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    timeout: float = 60.0,
) -> LLMResponse:
    """One-shot chat convenience function.

    Args:
        prompt: The user message.
        system: System prompt.
        provider_name: Provider to use (or env ``LLM_PROVIDER``).
        model: Optional model override.
        temperature: Sampling temperature.
        max_tokens: Max completion tokens.
        timeout: Request timeout in seconds.

    Returns:
        An :class:`LLMResponse`.
    """
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]
    provider = create_provider(provider_name)
    return chat_with_retry(
        messages,
        provider=provider,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Manual smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    print("=== Token estimation ===")
    samples = [
        "Hello, how are you today?",
        "人工智能正在改变世界",
        "Mix of English and 中文 content here!",
    ]
    for s in samples:
        print(f"  {s!r}  →  ~{estimate_tokens(s)} tokens")

    print()
    print("=== Cost calculation ===")
    sample_usage = Usage(prompt_tokens=500, completion_tokens=200)
    for name in _PROVIDER_CONFIGS:
        cost = calculate_cost(sample_usage, provider=name)
        print(f"  {name}: 500p+200c → ${cost:.6f}")

    print()
    print("=== Provider creation ===")
    for name in _PROVIDER_CONFIGS:
        p = create_provider(name)
        print(f"  {name}: base_url={p.base_url} model={p.default_model}")

    print()
    print("=== Quick chat (requires valid API key) ===")
    provider_name = os.environ.get("LLM_PROVIDER", "deepseek")
    api_key_env = _PROVIDER_CONFIGS[provider_name]["api_key_env"]
    if os.environ.get(api_key_env):
        resp = quick_chat("Say 'hello world' and nothing else.")
        print(f"  provider: {resp.provider}")
        print(f"  model:    {resp.model}")
        print(f"  content:  {resp.content!r}")
        print(f"  usage:    {resp.usage}")
        print(f"  cost:     ${calculate_cost(resp.usage, resp.provider):.6f}")
    else:
        print(f"  Skipped: {api_key_env} not set")

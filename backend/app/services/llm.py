"""
backend/app/services/llm.py
Resilient LLM service supporting Anthropic Claude, Google Gemini, and OpenAI:
  - Tenacity bounded retries with exponential backoff
  - Strict JSON extraction and Pydantic model validation
  - One automatic repair retry with specific error feedback on malformed JSON
  - Disk-based caching by SHA-256 hash of (prompt, model, rubric_version)
  - Detailed token, latency, and cost accounting
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Optional, Type, TypeVar

from pydantic import BaseModel
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config.settings import get_settings

logger = logging.getLogger("pw_qa.services.llm")

T = TypeVar("T", bound=BaseModel)


class LLMCallResult(BaseModel):
    success: bool
    data: Optional[Dict[str, Any]] = None
    raw_text: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    latency_ms: int = 0
    from_cache: bool = False
    attempts: int = 1
    model: str = ""
    price_version: str = "v1-2024-10"
    error_detail: Optional[str] = None


def _clean_json_text(text: str) -> str:
    """Extract JSON content from potential markdown fences."""
    text = text.strip()
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text


def _compute_cache_key(prompt_str: str, model: str, rubric_version: int = 1) -> str:
    content = f"{model}:{rubric_version}:{prompt_str}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class BaseLLMClient(ABC):
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        settings = get_settings()
        self.api_key = api_key or ""
        self.model = model or ""
        self.timeout_seconds = timeout_seconds
        self.cache_dir = settings.cache_path / "llm"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @abstractmethod
    def _execute_api_call(self, system: str, prompt: str) -> tuple[str, int, int]:
        """Execute raw API call returning (response_text, input_tokens, output_tokens)."""
        pass

    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        schema_cls: Type[T],
        rubric_version: int = 1,
        bypass_cache: bool = False,
    ) -> tuple[T, LLMCallResult]:
        """
        Call LLM with structured JSON parsing and Pydantic validation.
        Features 1 automatic repair retry on malformed JSON or validation failure.
        """
        settings = get_settings()
        combined_prompt = f"SYSTEM:\n{system_prompt}\nUSER:\n{user_prompt}"
        cache_key = _compute_cache_key(combined_prompt, self.model, rubric_version)
        cache_file = self.cache_dir / f"{cache_key}.json"

        # 1. Check cache
        if not bypass_cache and cache_file.exists():
            try:
                cached_data = json.loads(cache_file.read_text(encoding="utf-8"))
                parsed = schema_cls.model_validate(cached_data["data"])
                result = LLMCallResult(
                    success=True,
                    data=cached_data["data"],
                    raw_text=cached_data.get("raw_text"),
                    input_tokens=cached_data.get("input_tokens", 0),
                    output_tokens=cached_data.get("output_tokens", 0),
                    estimated_cost_usd=cached_data.get("estimated_cost_usd", 0.0),
                    latency_ms=0,
                    from_cache=True,
                    attempts=1,
                    model=self.model,
                    price_version=cached_data.get("price_version", settings.llm_price_version),
                )
                logger.info("LLM cache hit: %s", cache_key)
                return parsed, result
            except Exception as e:
                logger.warning("Corrupted LLM cache file %s: %s; re-calling", cache_file, e)
                cache_file.unlink(missing_ok=True)

        # 2. First attempt
        t_start = time.perf_counter()
        attempts = 1
        raw_text = ""
        in_tok, out_tok = 0, 0
        error_detail = None

        try:
            raw_text, in_tok, out_tok = self._execute_api_call(system_prompt, user_prompt)
            clean_json = _clean_json_text(raw_text)
            parsed_dict = json.loads(clean_json)
            parsed_model = schema_cls.model_validate(parsed_dict)
        except Exception as first_err:
            logger.warning("Attempt 1 failed (%s). Triggering repair retry...", first_err)
            error_detail = str(first_err)
            attempts = 2

            # 3. Automatic repair retry
            repair_user_prompt = (
                f"{user_prompt}\n\n"
                f"[SYSTEM NOTICE - ATTEMPT 1 FAILED]\n"
                f"Your previous output failed validation with error:\n{first_err}\n"
                f"Your previous output was:\n{raw_text[:1000]}\n\n"
                f"Output ONLY valid JSON matching the exact schema."
            )
            try:
                raw_text, in2, out2 = self._execute_api_call(system_prompt, repair_user_prompt)
                in_tok += in2
                out_tok += out2
                clean_json = _clean_json_text(raw_text)
                parsed_dict = json.loads(clean_json)
                parsed_model = schema_cls.model_validate(parsed_dict)
            except Exception as second_err:
                logger.error("Attempt 2 repair also failed: %s", second_err)
                latency_ms = int((time.perf_counter() - t_start) * 1000)
                cost_usd = (in_tok * settings.llm_cost_per_million_input_usd + out_tok * settings.llm_cost_per_million_output_usd) / 1_000_000
                res = LLMCallResult(
                    success=False,
                    data=None,
                    raw_text=raw_text,
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    estimated_cost_usd=cost_usd,
                    latency_ms=latency_ms,
                    from_cache=False,
                    attempts=attempts,
                    model=self.model,
                    price_version=settings.llm_price_version,
                    error_detail=f"Attempt 1: {error_detail} | Attempt 2: {second_err}",
                )
                raise RuntimeError(f"{self.model} structured generation failed after {attempts} attempts: {second_err}") from second_err

        # Successful generation
        latency_ms = int((time.perf_counter() - t_start) * 1000)
        cost_usd = (in_tok * settings.llm_cost_per_million_input_usd + out_tok * settings.llm_cost_per_million_output_usd) / 1_000_000

        result_meta = LLMCallResult(
            success=True,
            data=parsed_model.model_dump(),
            raw_text=raw_text,
            input_tokens=in_tok,
            output_tokens=out_tok,
            estimated_cost_usd=cost_usd,
            latency_ms=latency_ms,
            from_cache=False,
            attempts=attempts,
            model=self.model,
            price_version=settings.llm_price_version,
        )

        # Save to cache
        try:
            cache_file.write_text(
                json.dumps(result_meta.model_dump(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("Could not write LLM cache: %s", e)

        return parsed_model, result_meta


class ClaudeClient(BaseLLMClient):
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        settings = get_settings()
        super().__init__(
            api_key=api_key or settings.anthropic_api_key,
            model=model or settings.claude_model or "claude-3-5-sonnet-20241022",
            timeout_seconds=timeout_seconds,
        )
        self._client = None

    @property
    def client(self):
        if self._client is None:
            if not self.api_key:
                raise ValueError("ANTHROPIC_API_KEY is not set.")
            import anthropic
            self._client = anthropic.Anthropic(
                api_key=self.api_key,
                timeout=self.timeout_seconds,
            )
        return self._client

    def _execute_api_call(self, system: str, prompt: str) -> tuple[str, int, int]:
        import anthropic

        @retry(
            retry=retry_if_exception_type((
                anthropic.RateLimitError,
                anthropic.InternalServerError,
                anthropic.APIConnectionError,
                anthropic.APITimeoutError,
            )),
            wait=wait_exponential(multiplier=1, min=2, max=10),
            stop=stop_after_attempt(3),
            reraise=True,
        )
        def _call():
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            text = ""
            if response.content:
                first_block = response.content[0]
                if hasattr(first_block, "text"):
                    text = str(getattr(first_block, "text", ""))
            in_tokens = response.usage.input_tokens
            out_tokens = response.usage.output_tokens
            return text, in_tokens, out_tokens

        return _call()


class GeminiClient(BaseLLMClient):
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        settings = get_settings()
        key = api_key or settings.gemini_api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        super().__init__(
            api_key=key or "",
            model=model or settings.gemini_model or "gemini-2.5-flash",
            timeout_seconds=timeout_seconds,
        )
        self._client = None

    @property
    def client(self):
        if self._client is None:
            if not self.api_key:
                raise ValueError("GEMINI_API_KEY is not set. Add GEMINI_API_KEY to your .env file.")
            from google import genai
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def _execute_api_call(self, system: str, prompt: str) -> tuple[str, int, int]:
        from google.genai import types

        @retry(
            wait=wait_exponential(multiplier=1, min=2, max=10),
            stop=stop_after_attempt(3),
            reraise=True,
        )
        def _call():
            config = types.GenerateContentConfig(
                system_instruction=system if system else None,
                response_mime_type="application/json",
                temperature=0.0,
            )
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=config,
            )
            text = response.text or ""
            in_tokens = 0
            out_tokens = 0
            if response.usage_metadata:
                in_tokens = getattr(response.usage_metadata, "prompt_token_count", 0) or 0
                out_tokens = getattr(response.usage_metadata, "candidates_token_count", 0) or 0
            return text, in_tokens, out_tokens

        return _call()


class OpenAIClient(BaseLLMClient):
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        settings = get_settings()
        key = api_key or settings.openai_api_key or os.environ.get("OPENAI_API_KEY")
        super().__init__(
            api_key=key or "",
            model=model or settings.openai_model or "gpt-4o-mini",
            timeout_seconds=timeout_seconds,
        )
        self._client = None

    @property
    def client(self):
        if self._client is None:
            if not self.api_key:
                raise ValueError("OPENAI_API_KEY is not set.")
            import openai
            self._client = openai.OpenAI(api_key=self.api_key)
        return self._client

    def _execute_api_call(self, system: str, prompt: str) -> tuple[str, int, int]:
        @retry(
            wait=wait_exponential(multiplier=1, min=2, max=10),
            stop=stop_after_attempt(3),
            reraise=True,
        )
        def _call():
            response = self.client.chat.completions.create(
                model=self.model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
            )
            text = response.choices[0].message.content or ""
            in_tokens = response.usage.prompt_tokens if response.usage else 0
            out_tokens = response.usage.completion_tokens if response.usage else 0
            return text, in_tokens, out_tokens

        return _call()


def get_llm_client() -> BaseLLMClient:
    """Factory function returning the active LLM provider client."""
    settings = get_settings()
    provider = settings.llm_provider.lower().strip()

    if provider == "gemini" or settings.gemini_api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return GeminiClient()
    if provider == "openai" or settings.openai_api_key or os.environ.get("OPENAI_API_KEY"):
        return OpenAIClient()
    return ClaudeClient()

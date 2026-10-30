import os
import sys
import json
import time
import random
import logging
import requests
from abc import ABC, abstractmethod
from typing import Optional, List, Tuple, Dict, Any
from dotenv import load_dotenv

load_dotenv()

# Configure Windows UTF-8 console output if applicable
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass


class BaseLLMProvider(ABC):
    """Abstract base class for all LLM providers"""

    def __init__(self, api_key: Optional[str] = None, default_model: Optional[str] = None):
        self.api_key = api_key
        self.default_model = default_model

    @abstractmethod
    def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: float = 0.3,
        system_prompt: Optional[str] = None,
    ) -> str:
        """Generate response from the model"""
        pass

    @abstractmethod
    def get_fallback_models(self) -> List[str]:
        """Return list of fallback models for this provider"""
        pass


class GeminiProvider(BaseLLMProvider):
    """Google Gemini LLM Provider using google-genai SDK"""

    def __init__(self, api_key: Optional[str] = None, default_model: Optional[str] = None):
        super().__init__(
            api_key=api_key or os.getenv("GEMINI_API_KEY"),
            default_model=default_model or os.getenv("LLM_MODEL", "gemini-3.7-flash"),
        )
        try:
            from google import genai
            self.client = genai.Client(api_key=self.api_key) if self.api_key else None
        except ImportError:
            self.client = None

    def get_fallback_models(self) -> List[str]:
        return [self.default_model, "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-flash-latest"]

    def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: float = 0.3,
        system_prompt: Optional[str] = None,
    ) -> str:
        if not self.client:
            from google import genai
            self.client = genai.Client(api_key=self.api_key)

        from google.genai.types import GenerateContentConfig

        target_model = model or self.default_model
        config = GenerateContentConfig(temperature=temperature)
        if system_prompt:
            config.system_instruction = system_prompt

        response = self.client.models.generate_content(
            model=target_model,
            contents=prompt,
            config=config,
        )

        if hasattr(response, "text") and response.text:
            return response.text
        elif hasattr(response, "candidates") and response.candidates:
            candidate = response.candidates[0]
            if hasattr(candidate, "content") and hasattr(candidate.content, "parts"):
                parts_text = "".join(
                    part.text for part in candidate.content.parts if hasattr(part, "text") and part.text
                )
                if parts_text:
                    return parts_text

        return str(response)


class OpenAIProvider(BaseLLMProvider):
    """OpenAI Provider (GPT-4o, GPT-4o-mini, o1, etc.)"""

    def __init__(self, api_key: Optional[str] = None, default_model: Optional[str] = None, base_url: Optional[str] = None):
        super().__init__(
            api_key=api_key or os.getenv("OPENAI_API_KEY"),
            default_model=default_model or os.getenv("LLM_MODEL", "gpt-4o-mini"),
        )
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")

    def get_fallback_models(self) -> List[str]:
        return [self.default_model, "gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"]

    def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: float = 0.3,
        system_prompt: Optional[str] = None,
    ) -> str:
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is not set.")

        target_model = model or self.default_model
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": target_model,
            "messages": messages,
            "temperature": temperature,
        }

        resp = requests.post(f"{self.base_url}/chat/completions", headers=headers, json=payload, timeout=60)
        if resp.status_code != 200:
            raise Exception(f"OpenAI API Error [{resp.status_code}]: {resp.text}")

        data = resp.json()
        return data["choices"][0]["message"]["content"]


class AnthropicProvider(BaseLLMProvider):
    """Anthropic Claude Provider (Claude 3.5 Sonnet, Claude 3 Haiku, etc.)"""

    def __init__(self, api_key: Optional[str] = None, default_model: Optional[str] = None):
        super().__init__(
            api_key=api_key or os.getenv("ANTHROPIC_API_KEY"),
            default_model=default_model or os.getenv("LLM_MODEL", "claude-3-5-sonnet-20241022"),
        )
        self.base_url = "https://api.anthropic.com/v1"

    def get_fallback_models(self) -> List[str]:
        return [self.default_model, "claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022"]

    def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: float = 0.3,
        system_prompt: Optional[str] = None,
    ) -> str:
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY is not set.")

        target_model = model or self.default_model
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

        payload = {
            "model": target_model,
            "max_tokens": 4096,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_prompt:
            payload["system"] = system_prompt

        resp = requests.post(f"{self.base_url}/messages", headers=headers, json=payload, timeout=60)
        if resp.status_code != 200:
            raise Exception(f"Anthropic API Error [{resp.status_code}]: {resp.text}")

        data = resp.json()
        return data["content"][0]["text"]


class OllamaOrCompatibleProvider(BaseLLMProvider):
    """OpenAI-compatible Provider (Groq, DeepSeek, Local Ollama, OpenRouter)"""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        default_model: Optional[str] = None,
        fallback_models: Optional[List[str]] = None,
    ):
        base = base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        if not base.endswith("/v1") and "11434" in base:
            base = f"{base.rstrip('/')}/v1"
        super().__init__(
            api_key=api_key or os.getenv("GROQ_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or "ollama",
            default_model=default_model or os.getenv("LLM_MODEL", "openai/gpt-oss-120b"),
        )
        self.base_url = base.rstrip("/")
        self.fallback_models = fallback_models or [self.default_model]

    def get_fallback_models(self) -> List[str]:
        return self.fallback_models

    def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: float = 0.3,
        system_prompt: Optional[str] = None,
    ) -> str:
        target_model = model or self.default_model
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": target_model,
            "messages": messages,
            "temperature": temperature,
        }

        resp = requests.post(f"{self.base_url}/chat/completions", headers=headers, json=payload, timeout=90)
        if resp.status_code != 200:
            raise Exception(f"Compatible LLM API Error [{resp.status_code}]: {resp.text}")

        data = resp.json()
        return data["choices"][0]["message"]["content"]


def get_llm_provider(
    provider_name: Optional[str] = None,
    model_name: Optional[str] = None,
    api_key: Optional[str] = None,
) -> BaseLLMProvider:
    """Factory function to get the appropriate LLM provider instance"""
    provider = (provider_name or os.getenv("LLM_PROVIDER", "")).strip().lower()

    # Auto-detection based on configured keys if provider not specified
    if not provider:
        if os.getenv("GEMINI_API_KEY") or (api_key and api_key.startswith("AQ.")):
            provider = "gemini"
        elif os.getenv("OPENAI_API_KEY") or (api_key and api_key.startswith("sk-")):
            provider = "openai"
        elif os.getenv("ANTHROPIC_API_KEY") or (api_key and api_key.startswith("sk-ant-")):
            provider = "anthropic"
        elif os.getenv("GROQ_API_KEY") or (api_key and api_key.startswith("gsk_")):
            provider = "groq"
        elif os.getenv("OLLAMA_BASE_URL"):
            provider = "ollama"
        else:
            provider = "gemini"

    if provider in ["gemini", "google", "google_genai"]:
        return GeminiProvider(api_key=api_key, default_model=model_name)
    elif provider in ["openai", "gpt"]:
        return OpenAIProvider(api_key=api_key, default_model=model_name)
    elif provider in ["anthropic", "claude"]:
        return AnthropicProvider(api_key=api_key, default_model=model_name)
    elif provider in ["groq"]:
        return OllamaOrCompatibleProvider(
            base_url="https://api.groq.com/openai/v1",
            api_key=api_key or os.getenv("GROQ_API_KEY"),
            default_model=model_name or "openai/gpt-oss-120b",
            fallback_models=["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b", "groq/compound"],
        )
    elif provider in ["deepseek"]:
        return OllamaOrCompatibleProvider(
            base_url="https://api.deepseek.com/v1",
            api_key=api_key or os.getenv("DEEPSEEK_API_KEY"),
            default_model=model_name or "deepseek-chat",
        )
    elif provider in ["ollama", "local"]:
        return OllamaOrCompatibleProvider(
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
            api_key="ollama",
            default_model=model_name or "llama3",
        )
    else:
        # Fallback to Gemini
        return GeminiProvider(api_key=api_key, default_model=model_name)


def get_available_providers() -> List[BaseLLMProvider]:
    """Return all configured providers in priority order (primary provider first, followed by fallbacks)"""
    primary = get_llm_provider()
    providers = [primary]
    existing_types = {primary.__class__.__name__}

    # Check for secondary fallback providers configured with API keys
    if os.getenv("GROQ_API_KEY") and "OllamaOrCompatibleProvider" not in existing_types:
        providers.append(get_llm_provider("groq"))
        existing_types.add("OllamaOrCompatibleProvider")
    if os.getenv("OPENAI_API_KEY") and "OpenAIProvider" not in existing_types:
        providers.append(get_llm_provider("openai"))
        existing_types.add("OpenAIProvider")
    if os.getenv("ANTHROPIC_API_KEY") and "AnthropicProvider" not in existing_types:
        providers.append(get_llm_provider("anthropic"))
        existing_types.add("AnthropicProvider")
    if os.getenv("GEMINI_API_KEY") and "GeminiProvider" not in existing_types:
        providers.append(get_llm_provider("gemini"))
        existing_types.add("GeminiProvider")

    return providers


def generate_llm_response(
    prompt: str,
    temperature: float = 0.3,
    retries: int = 2,
    base_delay: int = 2,
    provider: Optional[BaseLLMProvider] = None,
    system_prompt: Optional[str] = None,
) -> Tuple[str, int, bool]:
    """
    Unified model-agnostic generation function with cross-provider failover.
    If primary provider (e.g. Gemini) runs out of quota/tokens, automatically switches to secondary (e.g. Groq).
    Returns: (generated_text, attempt_count, is_fallback)
    """
    providers = [provider] if provider else get_available_providers()
    attempt_count = 0
    is_fallback = False

    for prov_idx, llm in enumerate(providers):
        if prov_idx > 0:
            is_fallback = True
            print(f"🔄 [Failover] Switching to backup provider: {llm.__class__.__name__} ({llm.default_model})")

        models = llm.get_fallback_models()
        for model in models:
            for attempt in range(1, retries + 2):
                attempt_count += 1
                try:
                    text = llm.generate(
                        prompt=prompt,
                        model=model,
                        temperature=temperature,
                        system_prompt=system_prompt,
                    )
                    return text, attempt_count, is_fallback

                except Exception as e:
                    err_msg = str(e)
                    logging.error(f"[LLM API Error] Provider={llm.__class__.__name__} Model={model} Attempt={attempt}: {err_msg}")

                    if "503" in err_msg or "overloaded" in err_msg.lower() or "timeout" in err_msg.lower():
                        if attempt <= retries:
                            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 1)
                            time.sleep(delay)
                            continue
                        else:
                            break
                    elif "429" in err_msg or "quota" in err_msg.lower() or "rate_limit" in err_msg.lower() or "resource_exhausted" in err_msg.lower():
                        print(f"🚫 [Quota Exceeded] Provider {llm.__class__.__name__} rate limit/quota reached for {model}.")
                        # If quota exhausted on this model, try next model or next provider immediately
                        break
                    else:
                        # Hard error, try next provider
                        break

    return "⚠️ All configured AI providers (Gemini, Groq, OpenAI) are currently unavailable or quota-exhausted.", attempt_count, True

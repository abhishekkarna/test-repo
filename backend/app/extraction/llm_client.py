"""
Provider-agnostic LLM client.
Switch between Ollama (local Llama) and Anthropic (Claude) via LLM_PROVIDER env var.
"""

import logging
from abc import ABC, abstractmethod

from app.config import settings

logger = logging.getLogger(__name__)


class LLMClient(ABC):
    @abstractmethod
    def complete(self, system: str, user: str, max_tokens: int = 2048) -> str:
        """Return the assistant text response."""


class OllamaClient(LLMClient):
    def __init__(self):
        import ollama
        self._ollama = ollama
        self.model = settings.ollama_model
        self.base_url = settings.ollama_base_url

    def complete(self, system: str, user: str, max_tokens: int = 2048) -> str:
        response = self._ollama.Client(host=self.base_url).chat(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            options={"num_predict": max_tokens},
        )
        return response["message"]["content"].strip()


class AnthropicClient(LLMClient):
    def __init__(self):
        import anthropic
        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self.model = settings.anthropic_model

    def complete(self, system: str, user: str, max_tokens: int = 2048) -> str:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return response.content[0].text.strip()


def get_llm_client() -> LLMClient:
    provider = settings.llm_provider.lower()
    if provider == "anthropic":
        logger.info("Using Anthropic (%s)", settings.anthropic_model)
        return AnthropicClient()
    logger.info("Using Ollama (%s @ %s)", settings.ollama_model, settings.ollama_base_url)
    return OllamaClient()


# Module-level singleton — instantiated once at startup
llm = get_llm_client()

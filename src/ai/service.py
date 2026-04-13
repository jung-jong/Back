from dataclasses import dataclass
import json
import math

import httpx
from openai import AsyncOpenAI

from core.config import settings


@dataclass(frozen=True)
class VectorDocument:
    id: str
    text: str
    metadata: dict


@dataclass(frozen=True)
class RetrievedContext:
    text: str
    metadata: dict
    score: float | None = None


class AIService:
    def __init__(self) -> None:
        self._openai_client: AsyncOpenAI | None = None

    @property
    def provider(self) -> str:
        return settings.ai_provider.lower()

    @property
    def embedding_provider(self) -> str:
        return settings.embedding_provider.lower()

    @property
    def embedding_model_name(self) -> str:
        if self.embedding_provider == "gemini":
            return settings.gemini_embedding_model
        if self.embedding_provider == "openai":
            return settings.openai_embedding_model
        return settings.ollama_embedding_model

    @property
    def is_embedding_provider_configured(self) -> bool:
        if self.embedding_provider == "ollama":
            return bool(settings.ollama_base_url and settings.ollama_embedding_model)
        if self.embedding_provider == "openai":
            return bool(settings.openai_api_key)
        if self.embedding_provider == "gemini":
            return bool(settings.gemini_api_key and settings.gemini_embedding_model)
        return False

    def _get_openai_client(self) -> AsyncOpenAI:
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        if self._openai_client is None:
            self._openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
        return self._openai_client

    async def embed_texts(
        self,
        texts: list[str],
        task_type: str = "SEMANTIC_SIMILARITY",
    ) -> list[list[float]]:
        if self.embedding_provider == "ollama":
            return await self._embed_texts_with_ollama(texts)
        if self.embedding_provider == "gemini":
            return await self._embed_texts_with_gemini(texts, task_type=task_type)
        if self.embedding_provider != "openai":
            raise RuntimeError(
                f"Unsupported EMBEDDING_PROVIDER: {settings.embedding_provider}",
            )

        client = self._get_openai_client()
        response = await client.embeddings.create(
            model=settings.openai_embedding_model,
            input=texts,
        )
        return [item.embedding for item in response.data]

    async def _embed_texts_with_ollama(self, texts: list[str]) -> list[list[float]]:
        url = f"{settings.ollama_base_url.rstrip('/')}/api/embed"
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                url,
                json={
                    "model": settings.ollama_embedding_model,
                    "input": texts,
                },
            )
            response.raise_for_status()
        embeddings = response.json().get("embeddings") or []
        if len(embeddings) != len(texts):
            raise RuntimeError("Ollama returned an unexpected embedding response")
        return embeddings

    async def _embed_texts_with_gemini(
        self,
        texts: list[str],
        task_type: str = "SEMANTIC_SIMILARITY",
    ) -> list[list[float]]:
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")

        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{settings.gemini_embedding_model}:embedContent"
        )
        embeddings: list[list[float]] = []
        async with httpx.AsyncClient(timeout=120) as client:
            for text in texts:
                response = await client.post(
                    url,
                    headers={
                        "Content-Type": "application/json",
                        "x-goog-api-key": settings.gemini_api_key,
                    },
                    json={
                        "model": f"models/{settings.gemini_embedding_model}",
                        "content": {"parts": [{"text": text}]},
                        "taskType": task_type,
                        "output_dimensionality": settings.gemini_embedding_output_dimensionality,
                    },
                )
                response.raise_for_status()
                data = response.json()
                embedding = data.get("embedding") or {}
                values = embedding.get("values")
                if not values:
                    raise RuntimeError("Gemini returned an unexpected embedding response")
                embeddings.append(self._normalize_embedding(values))
        return embeddings

    def _normalize_embedding(self, values: list[float]) -> list[float]:
        magnitude = math.sqrt(sum(value * value for value in values))
        if magnitude == 0:
            return values
        return [value / magnitude for value in values]

    async def answer_with_contexts(
        self,
        question: str,
        contexts: list[RetrievedContext],
        system_prompt: str | None = None,
    ) -> str:
        context_text = "\n\n".join(
            f"[Source {index + 1}]\n{context.text}"
            for index, context in enumerate(contexts)
            if context.text
        )
        prompt = (
            "You are Custom-TA, a careful teaching assistant. "
            "Answer only from the provided course context. "
            "If the context is insufficient, say you do not know from the material."
        )
        if system_prompt:
            prompt = f"{prompt}\n\nCourse instruction:\n{system_prompt}"

        if self.provider == "ollama":
            return await self._answer_with_ollama(prompt, question, context_text)
        if self.provider == "gemini":
            return await self._answer_with_gemini(prompt, question, context_text)
        if self.provider != "openai":
            raise RuntimeError(f"Unsupported AI_PROVIDER: {settings.ai_provider}")

        client = self._get_openai_client()
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": f"Course context:\n{context_text}\n\nQuestion:\n{question}",
                },
            ],
        )
        return response.choices[0].message.content or ""

    async def generate_plain_response(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        if self.provider == "ollama":
            return await self._answer_with_ollama(system_prompt, user_prompt, "")
        if self.provider == "gemini":
            return await self._answer_with_gemini(system_prompt, user_prompt, "")
        if self.provider != "openai":
            raise RuntimeError(f"Unsupported AI_PROVIDER: {settings.ai_provider}")

        client = self._get_openai_client()
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return response.choices[0].message.content or ""

    async def generate_json_response(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> dict:
        response_text = await self.generate_plain_response(system_prompt, user_prompt)
        try:
            return json.loads(response_text)
        except json.JSONDecodeError:
            start = response_text.find("{")
            end = response_text.rfind("}")
            if start == -1 or end == -1 or end <= start:
                raise
            return json.loads(response_text[start : end + 1])

    async def _answer_with_ollama(
        self,
        system_prompt: str,
        question: str,
        context_text: str,
    ) -> str:
        url = f"{settings.ollama_base_url.rstrip('/')}/api/chat"
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                url,
                json={
                    "model": settings.ollama_model,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {
                            "role": "user",
                            "content": (
                                f"Course context:\n{context_text}\n\n"
                                f"Question:\n{question}"
                            ),
                        },
                    ],
                },
            )
            response.raise_for_status()
        data = response.json()
        return data.get("message", {}).get("content", "")

    async def _answer_with_gemini(
        self,
        system_prompt: str,
        question: str,
        context_text: str,
    ) -> str:
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")

        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{settings.gemini_model}:generateContent"
        )
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "generationConfig": {
                "maxOutputTokens": settings.ai_max_output_tokens,
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": (
                                f"Course context:\n{context_text}\n\n"
                                f"Question:\n{question}"
                            ),
                        },
                    ],
                },
            ],
        }
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                url,
                params={"key": settings.gemini_api_key},
                json=payload,
            )
            response.raise_for_status()
        data = response.json()
        candidates = data.get("candidates") or []
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts") or []
        return "\n".join(part.get("text", "") for part in parts).strip()

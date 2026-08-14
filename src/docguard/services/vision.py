"""Provider-neutral visual understanding and durable response caching."""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from docguard.settings import Settings


@dataclass(frozen=True)
class VisionResponse:
    adapter_id: str
    model: str
    raw_response: str


class VisionAdapter(Protocol):
    adapter_id: str
    model: str

    def describe(self, image: bytes, prompt: str, *, media_type: str = "image/png") -> VisionResponse: ...


class QwenVisionAdapter:
    """Qwen via Alibaba Model Studio's OpenAI-compatible Chat API."""

    adapter_id = "qwen-openai-compatible"

    def __init__(self, *, api_key: str | None = None, base_url: str | None = None, model: str | None = None) -> None:
        settings = Settings.from_environment()
        self.api_key = api_key or settings.qwen_api_key
        self.base_url = base_url or settings.qwen_base_url
        self.model = model or settings.qwen_vision_model

    def describe(self, image: bytes, prompt: str, *, media_type: str = "image/png") -> VisionResponse:
        if not self.api_key:
            raise RuntimeError("DASHSCOPE_API_KEY is required for Qwen visual review")
        from openai import OpenAI

        image_url = f"data:{media_type};base64," + base64.b64encode(image).decode("ascii")
        response = OpenAI(api_key=self.api_key, base_url=self.base_url).chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": image_url}},
                {"type": "text", "text": prompt},
            ]}],
            extra_body={"enable_thinking": True},
        )
        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("Qwen returned no visual response content")
        # Keep the provider envelope for traceability while preserving the
        # model's original visible content without a second parsing step.
        raw = json.dumps({"content": content, "provider_response": response.model_dump(mode="json")}, ensure_ascii=False)
        return VisionResponse(self.adapter_id, self.model, raw)


class VisionResponseCache:
    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS vision_response_cache (
                    cache_key TEXT PRIMARY KEY, image_sha256 TEXT NOT NULL,
                    prompt_sha256 TEXT NOT NULL, adapter_id TEXT NOT NULL,
                    model TEXT NOT NULL, raw_response TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)

    @classmethod
    def from_environment(cls) -> "VisionResponseCache":
        return cls(Settings.from_environment().database_path)

    def get_or_create(self, image: bytes, prompt: str, adapter: VisionAdapter, *, media_type: str = "image/png") -> tuple[VisionResponse, bool]:
        image_sha256 = hashlib.sha256(image).hexdigest()
        prompt_sha256 = hashlib.sha256(prompt.encode()).hexdigest()
        cache_key = hashlib.sha256(f"{image_sha256}:{prompt_sha256}:{adapter.adapter_id}:{adapter.model}".encode()).hexdigest()
        with self._connect() as connection:
            row = connection.execute("SELECT raw_response FROM vision_response_cache WHERE cache_key = ?", (cache_key,)).fetchone()
            if row:
                return VisionResponse(adapter.adapter_id, adapter.model, row["raw_response"]), True
        response = adapter.describe(image, prompt, media_type=media_type)
        with self._connect() as connection:
            connection.execute("INSERT OR IGNORE INTO vision_response_cache VALUES (?, ?, ?, ?, ?, ?, ?)", (cache_key, image_sha256, prompt_sha256, response.adapter_id, response.model, response.raw_response, datetime.now(timezone.utc).isoformat()))
        return response, False

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

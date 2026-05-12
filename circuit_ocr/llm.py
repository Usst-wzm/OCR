from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Any

from openai import OpenAI

from .json_utils import component_list_from_payload, extract_json_payload
from .models import ComponentCandidate, Tile
from .prompts import PAGE_REVIEW_SYSTEM_PROMPT, TILE_SYSTEM_PROMPT


class VisionExtractor:
    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        max_retries: int = 2,
        request_timeout: float = 120.0,
    ) -> None:
        self.model = model or os.getenv("OPENAI_MODEL") or "gpt-4o-mini"
        key = api_key or os.getenv("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY is required for model OCR")
        self.client = OpenAI(api_key=key, base_url=base_url or os.getenv("OPENAI_BASE_URL") or None)
        self.max_retries = max_retries
        self.request_timeout = request_timeout

    def extract_tile(self, tile: Tile, cache_path: Path) -> list[ComponentCandidate]:
        payload = self._cached_or_call(cache_path, lambda: self._call_tile(tile))
        if _is_error_payload(payload):
            return []
        return self._payload_to_candidates(payload, page=tile.page, source_tile=tile.tile_id, region=tile.region)

    def review_page(
        self,
        page: int,
        candidates: list[ComponentCandidate],
        cache_path: Path,
    ) -> list[ComponentCandidate]:
        if not candidates:
            return []
        payload = self._cached_or_call(cache_path, lambda: self._call_page_review(page, candidates))
        if _is_error_payload(payload):
            return candidates
        return self._payload_to_candidates(payload, page=page)

    def _cached_or_call(self, cache_path: Path, call: Any) -> Any:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        if cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                payload = call()
                cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                return payload
            except Exception as exc:  # noqa: BLE001 - surface final API/JSON error after retry
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(2**attempt)
        error_payload = {
            "error": "model_call_failed",
            "message": str(last_error),
            "components": [],
        }
        cache_path.with_suffix(".error.json").write_text(
            json.dumps(error_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[warn] model call failed; skipped and wrote {cache_path.with_suffix('.error.json')}: {last_error}")
        return error_payload

    def _call_tile(self, tile: Tile) -> Any:
        image_url = _image_data_url(Path(tile.path))
        user_prompt = (
            f"页码：{tile.page}\n"
            f"图块：{tile.tile_id}\n"
            f"图块在整页中的粗略区域：{tile.region}\n"
            "请对这个图块做全量 OCR，输出所有可读短文本。不要提前筛选元器件。"
        )
        text = self._chat(
            system_prompt=TILE_SYSTEM_PROMPT,
            user_content=[
                {"type": "text", "text": user_prompt},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        )
        return _parse_or_raise_with_raw(text)

    def _call_page_review(self, page: int, candidates: list[ComponentCandidate]) -> Any:
        body = json.dumps([item.to_dict() for item in candidates], ensure_ascii=False)
        text = self._chat(
            system_prompt=PAGE_REVIEW_SYSTEM_PROMPT,
            user_content=(
                f"页码：{page}\n"
                "候选列表如下，请整理为最终元器件名称列表：\n"
                f"{body}"
            ),
        )
        return _parse_or_raise_with_raw(text)

    def _chat(self, *, system_prompt: str, user_content: Any) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            timeout=self.request_timeout,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("empty model response")
        return content

    @staticmethod
    def _payload_to_candidates(
        payload: Any,
        *,
        page: int,
        source_tile: str = "",
        region: str = "",
    ) -> list[ComponentCandidate]:
        candidates: list[ComponentCandidate] = []
        for item in component_list_from_payload(payload):
            candidate = ComponentCandidate.from_mapping(
                item,
                page=page,
                source_tile=source_tile,
                fallback_region=region,
            )
            if candidate:
                candidates.append(candidate)
        return candidates


def _image_data_url(path: Path) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _parse_or_raise_with_raw(text: str) -> Any:
    try:
        return extract_json_payload(text)
    except Exception as exc:
        raise ValueError(f"{exc}; raw response preview: {text[:500]}") from exc


def _is_error_payload(payload: Any) -> bool:
    return isinstance(payload, dict) and payload.get("error") == "model_call_failed"

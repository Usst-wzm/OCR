from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .models import ComponentCandidate, Tile


class AliyunOCRExtractor:
    def __init__(
        self,
        *,
        access_key_id: str | None = None,
        access_key_secret: str | None = None,
        endpoint: str | None = None,
        security_token: str | None = None,
        max_retries: int = 2,
    ) -> None:
        self.max_retries = max_retries
        self.client = _create_client(
            access_key_id=access_key_id,
            access_key_secret=access_key_secret,
            endpoint=endpoint,
            security_token=security_token,
        )

    def extract_tile(self, tile: Tile, cache_path: Path) -> list[ComponentCandidate]:
        payload = self._cached_or_call(cache_path, lambda: self._call_tile(tile))
        if _is_error_payload(payload):
            return []
        return payload_to_candidates(payload, tile=tile)

    def review_page(
        self,
        page: int,
        candidates: list[ComponentCandidate],
        cache_path: Path,
    ) -> list[ComponentCandidate]:
        return candidates

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
            except Exception as exc:  # noqa: BLE001 - surface final API error after retry
                last_error = exc
                if _is_non_retryable_aliyun_error(exc):
                    break
                if attempt < self.max_retries:
                    time.sleep(2**attempt)
        error_payload = {
            "error": "aliyun_ocr_call_failed",
            "message": str(last_error),
            "components": [],
        }
        cache_path.with_suffix(".error.json").write_text(
            json.dumps(error_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[warn] aliyun OCR call failed; skipped and wrote {cache_path.with_suffix('.error.json')}: {last_error}")
        return error_payload

    def _call_tile(self, tile: Tile) -> dict[str, Any]:
        try:
            from alibabacloud_ocr_api20210707 import models as ocr_models
            from alibabacloud_tea_util import models as util_models
        except ImportError as exc:
            raise RuntimeError(
                "阿里云 OCR 依赖未安装，请先运行：python -m pip install -e ."
            ) from exc

        with Path(tile.path).open("rb") as image_file:
            request = ocr_models.RecognizeAllTextRequest(
                body=image_file,
                type=os.getenv("ALIYUN_OCR_TYPE") or "General",
                output_coordinate=os.getenv("ALIYUN_OCR_OUTPUT_COORDINATE") or "rectangle",
                output_oricoord=True,
            )
            response = self.client.recognize_all_text_with_options(request, util_models.RuntimeOptions())
        return normalize_response(response)


def payload_to_candidates(payload: Any, *, tile: Tile) -> list[ComponentCandidate]:
    data = parse_data_payload(payload)
    words = data.get("prism_wordsInfo") or data.get("wordsInfo") or data.get("word_info") or []
    candidates: list[ComponentCandidate] = []
    if isinstance(words, list):
        for item in words:
            if not isinstance(item, dict):
                continue
            text = str(
                item.get("word")
                or item.get("content")
                or item.get("text")
                or item.get("recText")
                or ""
            ).strip()
            if not text:
                continue
            confidence = item.get("prob") or item.get("confidence") or item.get("score") or 0.0
            candidates.append(
                ComponentCandidate(
                    page=tile.page,
                    component_name=text,
                    raw_text=text,
                    bbox_or_region=_bbox_from_word_info(item) or tile.region,
                    source_tile=tile.tile_id,
                    confidence=_coerce_confidence(confidence),
                    reason="aliyun recognize general OCR",
                )
            )
    if candidates:
        return candidates

    block_candidates = _candidates_from_all_text_blocks(data, tile=tile)
    if block_candidates:
        return block_candidates

    content = str(data.get("content") or data.get("Content") or data.get("text") or "").strip()
    return [
        ComponentCandidate(
            page=tile.page,
            component_name=line.strip(),
            raw_text=line.strip(),
            bbox_or_region=tile.region,
            source_tile=tile.tile_id,
            confidence=0.0,
            reason="aliyun recognize general OCR",
        )
        for line in content.splitlines()
        if line.strip()
    ]


def normalize_response(response: Any) -> dict[str, Any]:
    body = getattr(response, "body", response)
    if hasattr(body, "to_map"):
        body = body.to_map()
    elif hasattr(body, "__dict__"):
        body = {key: value for key, value in vars(body).items() if not key.startswith("_")}
    return parse_data_payload(body)


def parse_data_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, str):
        return _json_object_from_text(payload)
    if not isinstance(payload, dict):
        return {}
    data = payload.get("Data") or payload.get("data")
    if isinstance(data, str):
        return _json_object_from_text(data)
    if isinstance(data, dict):
        return data
    return payload


def _create_client(
    *,
    access_key_id: str | None,
    access_key_secret: str | None,
    endpoint: str | None,
    security_token: str | None,
) -> Any:
    try:
        from alibabacloud_ocr_api20210707.client import Client
        from alibabacloud_tea_openapi import models as open_api_models
    except ImportError as exc:
        raise RuntimeError(
            "阿里云 OCR 依赖未安装，请先运行：python -m pip install -e ."
        ) from exc

    key_id = access_key_id or os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID") or os.getenv("ALIYUN_ACCESS_KEY_ID")
    key_secret = (
        access_key_secret
        or os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET")
        or os.getenv("ALIYUN_ACCESS_KEY_SECRET")
    )
    if not key_id or not key_secret:
        raise RuntimeError(
            "阿里云 OCR 需要 ALIBABA_CLOUD_ACCESS_KEY_ID 和 ALIBABA_CLOUD_ACCESS_KEY_SECRET"
        )

    config = open_api_models.Config(
        access_key_id=key_id,
        access_key_secret=key_secret,
        security_token=security_token or os.getenv("ALIBABA_CLOUD_SECURITY_TOKEN") or None,
    )
    config.endpoint = endpoint or os.getenv("ALIYUN_OCR_ENDPOINT") or "ocr-api.cn-hangzhou.aliyuncs.com"
    return Client(config)


def _json_object_from_text(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        return {}
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            return {"content": stripped}
        payload = json.loads(stripped[start : end + 1])
    return payload if isinstance(payload, dict) else {}


def _bbox_from_word_info(item: dict[str, Any]) -> str:
    for key in ("pos", "position", "bbox", "box", "prism_pos"):
        value = item.get(key)
        if value:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    keys = ("x", "y", "width", "height")
    if all(key in item for key in keys):
        return f"x={item['x']},y={item['y']},w={item['width']},h={item['height']}"
    return ""


def _candidates_from_all_text_blocks(data: dict[str, Any], *, tile: Tile) -> list[ComponentCandidate]:
    sub_images = data.get("SubImages") or data.get("subImages") or []
    candidates: list[ComponentCandidate] = []
    if not isinstance(sub_images, list):
        return candidates
    for sub_image in sub_images:
        if not isinstance(sub_image, dict):
            continue
        block_info = sub_image.get("BlockInfo") or sub_image.get("blockInfo") or {}
        if not isinstance(block_info, dict):
            continue
        block_details = block_info.get("BlockDetails") or block_info.get("blockDetails") or []
        if not isinstance(block_details, list):
            continue
        for block in block_details:
            if not isinstance(block, dict):
                continue
            text = str(block.get("BlockContent") or block.get("blockContent") or "").strip()
            if not text:
                continue
            candidates.append(
                ComponentCandidate(
                    page=tile.page,
                    component_name=text,
                    raw_text=text,
                    bbox_or_region=_bbox_from_all_text_block(block) or tile.region,
                    source_tile=tile.tile_id,
                    confidence=_coerce_confidence(block.get("BlockConfidence") or block.get("blockConfidence")),
                    reason="aliyun recognize all text OCR",
                )
            )
    return candidates


def _bbox_from_all_text_block(block: dict[str, Any]) -> str:
    rect = block.get("BlockRect") or block.get("blockRect")
    if rect:
        return json.dumps(rect, ensure_ascii=False, separators=(",", ":"))
    points = block.get("BlockPoints") or block.get("blockPoints")
    if points:
        return json.dumps(points, ensure_ascii=False, separators=(",", ":"))
    return ""


def _coerce_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    if confidence > 1:
        confidence /= 100
    return max(0.0, min(1.0, confidence))


def _is_error_payload(payload: Any) -> bool:
    return isinstance(payload, dict) and payload.get("error") == "aliyun_ocr_call_failed"


def _is_non_retryable_aliyun_error(exc: Exception) -> bool:
    text = str(exc)
    return any(
        code in text
        for code in (
            "ocrServiceNotOpen",
            "InvalidAccessKeyId",
            "InvalidAccessKeySecret",
            "Forbidden",
            "Unauthorized",
        )
    )

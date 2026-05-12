from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from openai import OpenAI

from .json_utils import component_list_from_payload, extract_json_payload
from .models import ComponentCandidate
from .postprocess import dedupe_candidates


COMPONENT_EXTRACTION_SYSTEM_PROMPT = """你是专业的汽车电路图、线束图和仪表图元器件名称抽取助手。
输入是 OCR 识别出的短文本列表，可能包含尺寸、说明句和噪声。
只抽取真正的元器件/电气对象名称，合并重复项，修正常见 OCR 错字，并分类。
保留：连接器/端子编号、传感器、开关/按键、继电器、控制器/ECU、仪表/液晶屏、警报/指示灯、电源、搭铁、保险、CAN 信号、线束、电机、阀、泵、喇叭、执行器。
过滤：纯数字、尺寸、公差、坐标、页码、普通说明句、操作说明、颜色/材料描述。
只输出 JSON：{"components":[{"component_name":"名称","category":"类别","raw_text":"来源文本","confidence":0.0}]}
不要输出解释文字。"""

LIGHT_NAMES_SYSTEM_PROMPT = """You extract component/electrical object names from OCR text for vehicle circuit drawings.
Input is one page of OCR snippets joined by separators.
Keep only real component or electrical object names, deduplicate them, and fix obvious OCR mistakes.
Keep connectors/terminals, sensors, switches/buttons, relays, ECU/controllers, instrument/display items, warning/indicator lamps, power, ground, fuse, CAN signals, harnesses, motors, valves, pumps, horns, and actuators.
Filter out pure numbers, dimensions, tolerances, page numbers, general prose, operating instructions, colors, and material descriptions.
Return ONLY one valid JSON object with exactly this shape: {"names":"name1,name2,name3"}.
Do not explain. Do not use markdown. Do not include any keys other than names."""


class TextComponentExtractor:
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
            raise RuntimeError("大模型抽取需要 OPENAI_API_KEY，或在界面里填写模型 API Key")
        self.client = OpenAI(api_key=key, base_url=base_url or os.getenv("OPENAI_BASE_URL") or None)
        self.max_retries = max_retries
        self.request_timeout = request_timeout

    def extract(
        self,
        ocr_items: list[ComponentCandidate],
        *,
        cache_dir: Path,
        chunk_size: int = 120,
    ) -> list[ComponentCandidate]:
        chunks = _chunk_items(ocr_items, chunk_size=chunk_size)
        candidates: list[ComponentCandidate] = []
        for index, chunk in enumerate(chunks, start=1):
            cache_path = cache_dir / f"chunk_{index:03d}.json"
            payload = self._cached_or_call(cache_path, lambda chunk=chunk: self._call_chunk(chunk))
            candidates.extend(self._payload_to_candidates(payload, chunk))
        return dedupe_candidates(candidates)

    def extract_by_page(
        self,
        ocr_items: list[ComponentCandidate],
        *,
        cache_dir: Path,
        progress_callback: Callable[[str], None] | None = None,
    ) -> list[ComponentCandidate]:
        candidates: list[ComponentCandidate] = []
        for page in sorted({item.page for item in ocr_items}):
            page_items = [item for item in ocr_items if item.page == page]
            cache_path = cache_dir / f"page_{page:03d}.json"
            compact_count = len(_compact_texts_for_model(page_items))
            started_at = time.perf_counter()
            if progress_callback:
                progress_callback(f"大模型开始处理第 {page} 页：发送 {compact_count} 条去重短文本")
            payload = self._cached_or_call(
                cache_path,
                lambda page=page, page_items=page_items: self._call_light_page(page, page_items),
            )
            if _is_error_payload(payload):
                if progress_callback:
                    progress_callback(f"大模型第 {page} 页失败：{payload.get('message', 'unknown error')}")
                continue
            page_candidates = self._light_payload_to_candidates(payload, page=page)
            candidates.extend(page_candidates)
            if progress_callback:
                progress_callback(
                    f"大模型完成第 {page} 页：输出 {len(page_candidates)} 个名称，用时 {time.perf_counter() - started_at:.2f}s"
                )
        return dedupe_candidates(candidates)

    def clean_names_by_page(
        self,
        candidates: list[ComponentCandidate],
        *,
        cache_dir: Path,
        progress_callback: Callable[[str], None] | None = None,
    ) -> list[ComponentCandidate]:
        cleaned: list[ComponentCandidate] = []
        for page in sorted({item.page for item in candidates}):
            page_items = [item for item in candidates if item.page == page]
            cache_path = cache_dir / f"page_{page:03d}.json"
            names = _compact_texts_for_model(page_items)
            started_at = time.perf_counter()
            if progress_callback:
                progress_callback(f"大模型开始清洗第 {page} 页候选：发送 {len(names)} 个本地候选名称")
            payload = self._cached_or_call(
                cache_path,
                lambda page=page, names=names: self._call_light_names(page, names),
            )
            if _is_error_payload(payload):
                if progress_callback:
                    progress_callback(f"大模型第 {page} 页候选清洗失败：{payload.get('message', 'unknown error')}")
                continue
            page_candidates = self._light_payload_to_candidates(payload, page=page)
            cleaned.extend(page_candidates)
            if progress_callback:
                progress_callback(
                    f"大模型完成第 {page} 页候选清洗：输出 {len(page_candidates)} 个名称，用时 {time.perf_counter() - started_at:.2f}s"
                )
        return dedupe_candidates(cleaned)

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
            except Exception as exc:  # noqa: BLE001 - surface final model/JSON error after retry
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(2**attempt)
        error_payload = {"error": "llm_component_extract_failed", "message": str(last_error), "components": []}
        cache_path.with_suffix(".error.json").write_text(
            json.dumps(error_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return error_payload

    def _call_chunk(self, chunk: list[ComponentCandidate]) -> Any:
        lines = [
            {
                "page": item.page,
                "text": item.raw_text or item.component_name,
                "source_tile": item.source_tile,
                "bbox_or_region": item.bbox_or_region,
                "confidence": item.confidence,
            }
            for item in chunk
            if item.raw_text or item.component_name
        ]
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            timeout=self.request_timeout,
            messages=[
                {"role": "system", "content": COMPONENT_EXTRACTION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": "请从以下 OCR 文本中抽取元器件/电气对象名称：\n"
                    + json.dumps(lines, ensure_ascii=False),
                },
            ],
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("empty model response")
        return extract_json_payload(content)

    def _call_page(self, page: int, page_items: list[ComponentCandidate]) -> Any:
        lines = _compact_texts_for_model(page_items)
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            timeout=self.request_timeout,
            messages=[
                {"role": "system", "content": COMPONENT_EXTRACTION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"第 {page} 页 OCR 短文本如下，请抽取、去重、分类元器件/电气对象名称，按 JSON 返回：\n"
                        + "，".join(lines)
                    ),
                },
            ],
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("empty model response")
        return extract_json_payload(content)

    def _call_light_page(self, page: int, page_items: list[ComponentCandidate]) -> Any:
        text = " | ".join(_compact_texts_for_model(page_items))
        return self._call_light_names(page, [text], already_joined=True)

    def _call_light_names(self, page: int, names: list[str], *, already_joined: bool = False) -> Any:
        text = names[0] if already_joined and names else " | ".join(names)
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            timeout=self.request_timeout,
            max_tokens=700,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": LIGHT_NAMES_SYSTEM_PROMPT},
                {"role": "user", "content": f"Page {page} text candidates, separated by |:\n{text}"},
            ],
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("empty model response")
        return extract_json_payload(content)

    @staticmethod
    def _payload_to_candidates(
        payload: Any,
        chunk: list[ComponentCandidate],
        *,
        default_page: int = 1,
    ) -> list[ComponentCandidate]:
        by_text = {
            (item.raw_text or item.component_name).strip(): item
            for item in chunk
            if (item.raw_text or item.component_name).strip()
        }
        candidates: list[ComponentCandidate] = []
        for item in component_list_from_payload(payload):
            raw_text = str(item.get("raw_text") or item.get("source_text") or item.get("text") or "").strip()
            source = by_text.get(raw_text)
            candidate = ComponentCandidate.from_mapping(
                item,
                page=source.page if source else default_page,
                source_tile=source.source_tile if source else "",
                fallback_region=source.bbox_or_region if source else "",
            )
            if candidate:
                if source:
                    candidate.source_tile = source.source_tile
                    candidate.bbox_or_region = source.bbox_or_region
                candidate.reason = candidate.reason or "LLM extracted from OCR text"
                candidates.append(candidate)
        return candidates

    @staticmethod
    def _light_payload_to_candidates(payload: Any, *, page: int) -> list[ComponentCandidate]:
        if isinstance(payload, dict):
            names_value = payload.get("names") or payload.get("component_names") or payload.get("result") or ""
        else:
            names_value = payload
        if isinstance(names_value, list):
            names = [str(item).strip() for item in names_value]
        else:
            names = _split_names_text(str(names_value))
        return [
            ComponentCandidate(
                page=page,
                component_name=name,
                category="待分类",
                raw_text=name,
                confidence=0.9,
                reason="LLM extracted compact names",
            )
            for name in names
            if name
        ]


def _chunk_items(items: list[ComponentCandidate], *, chunk_size: int) -> list[list[ComponentCandidate]]:
    useful = [item for item in items if item.raw_text or item.component_name]
    if not useful:
        return []
    return [useful[index : index + chunk_size] for index in range(0, len(useful), chunk_size)]


def _compact_texts_for_model(items: list[ComponentCandidate]) -> list[str]:
    texts: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = " ".join((item.raw_text or item.component_name).split())
        if not _should_send_text_to_model(text):
            continue
        key = text.lower()
        if key in seen:
            continue
        texts.append(text)
        seen.add(key)
    return texts


def compact_text_count_for_model(items: list[ComponentCandidate]) -> int:
    return len(_compact_texts_for_model(items))


def _should_send_text_to_model(text: str) -> bool:
    if len(text) < 2:
        return False
    if len(text) > 80:
        return False
    if all(char.isdigit() or char in " .,:;+-*/()[]{}<>%°℃" for char in text):
        return False
    return True


def _split_names_text(text: str) -> list[str]:
    normalized = (
        text.replace("\n", ",")
        .replace("、", ",")
        .replace("，", ",")
        .replace("；", ",")
        .replace(";", ",")
    )
    parts = []
    for part in normalized.split(","):
        cleaned = part.strip(" \t\r\n,，。[]【】()（）")
        if cleaned and cleaned not in parts:
            parts.append(cleaned)
    return parts


def _is_error_payload(payload: Any) -> bool:
    return isinstance(payload, dict) and payload.get("error") == "llm_component_extract_failed"

from __future__ import annotations

import re
from pathlib import Path

from .models import ComponentCandidate, repair_mojibake
from .postprocess import dedupe_candidates, looks_like_component, normalize_name


DEFAULT_TERMS = [
    "传感器",
    "开关",
    "继电器",
    "控制器",
    "仪表",
    "指示灯",
    "指示",
    "报警灯",
    "警报",
    "报警",
    "故障",
    "线束",
    "搭铁",
    "接地",
    "电源",
    "电瓶",
    "蓄电池",
    "保险",
    "熔断",
    "插头",
    "插接",
    "连接器",
    "端子",
    "端脚",
    "电机",
    "喇叭",
    "电磁阀",
    "阀",
    "泵",
    "灯",
    "屏",
    "按键",
    "信号",
    "温度",
    "液位",
    "车速",
    "转速",
    "ECU",
    "CAN",
    "ABS",
    "ASR",
    "EBS",
    "DPF",
    "STOP",
    "AdBlue",
]

CONNECTOR_RE = re.compile(r"\bX\d+[:：]\d+\b", re.IGNORECASE)
TOKEN_RE = re.compile(r"\b(CAN[-_ ]?[HL]|ABS\d*|ASR|EBS|DPF|STOP|ECU|VCU|BCM|AdBlue)\b", re.IGNORECASE)
LEADING_NOISE_RE = re.compile(
    r"^(?:\d+\s*)?(?:红色|黄色|绿色|蓝色|白色|黑色|棕色|橙色|灰色)?\s*(?:X\d+[:：]\d+\s*)?",
    re.IGNORECASE,
)
TRAILING_SIGNAL_RE = re.compile(
    r"(?:高电平|低电平|预留|CAN数据|集电极开路|输入信号|输出|信号输入|"
    r"\d+(\.\d+)?\s*(V|A|Hz|kbps|km/h|r/min).*)$",
    re.IGNORECASE,
)


def load_terms(path: Path | None = None) -> list[str]:
    if path is None:
        return DEFAULT_TERMS
    terms = []
    for line in path.read_text(encoding="utf-8").splitlines():
        cleaned = line.strip()
        if cleaned and not cleaned.startswith("#"):
            terms.append(cleaned)
    return terms or DEFAULT_TERMS


def extract_components_from_ocr(
    ocr_items: list[ComponentCandidate],
    *,
    terms: list[str] | None = None,
) -> list[ComponentCandidate]:
    active_terms = terms or DEFAULT_TERMS
    candidates: list[ComponentCandidate] = []
    for item in ocr_items:
        text = repair_mojibake(item.raw_text or item.component_name)
        for name in names_from_text(text, active_terms):
            candidates.append(
                ComponentCandidate(
                    page=item.page,
                    component_name=name,
                    category=_guess_category(name),
                    raw_text=text,
                    bbox_or_region=item.bbox_or_region,
                    source_tile=item.source_tile,
                    confidence=item.confidence,
                    reason="matched from OCR text",
                )
            )
    return dedupe_candidates(candidates)


def names_from_text(text: str, terms: list[str] | None = None) -> list[str]:
    active_terms = terms or DEFAULT_TERMS
    repaired = repair_mojibake(text)
    fragments = _split_fragments(repaired)
    names: list[str] = []
    for fragment in fragments:
        names.extend(_names_from_fragment(fragment, active_terms))
    return _dedupe_names(names)


def _names_from_fragment(fragment: str, terms: list[str]) -> list[str]:
    raw = repair_mojibake(fragment)
    cleaned = _clean_fragment(raw)
    if not cleaned:
        return []

    names: list[str] = []
    for connector in CONNECTOR_RE.findall(raw):
        names.append(connector.replace("：", ":"))

    for token in TOKEN_RE.findall(cleaned):
        names.append(token.replace("_", "-").replace(" ", "-").upper())

    for term in terms:
        if term.lower() in cleaned.lower():
            phrase = _phrase_around_term(cleaned, term)
            if phrase:
                names.append(phrase)

    return [normalize_name(name) for name in names if looks_like_component(name)]


def _split_fragments(text: str) -> list[str]:
    normalized = re.sub(r"[|｜]", " ", text)
    parts = re.split(r"[\t\r\n,，;；]+|\s{2,}", normalized)
    return [part.strip() for part in parts if part.strip()]


def _clean_fragment(fragment: str) -> str:
    cleaned = repair_mojibake(fragment)
    cleaned = cleaned.replace("—", "-").replace("－", "-")
    cleaned = LEADING_NOISE_RE.sub("", cleaned).strip(" -:：\t")
    cleaned = TRAILING_SIGNAL_RE.sub("", cleaned).strip(" -:：\t")
    return cleaned


def _phrase_around_term(text: str, term: str) -> str:
    cleaned = _clean_fragment(text)
    if len(cleaned) <= 24:
        return cleaned
    index = cleaned.lower().find(term.lower())
    if index < 0:
        return cleaned[:24]
    start = max(0, index - 8)
    end = min(len(cleaned), index + len(term) + 8)
    return cleaned[start:end].strip(" -:：")


def _dedupe_names(names: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for name in names:
        key = normalize_name(name).lower()
        if key and key not in seen:
            result.append(normalize_name(name))
            seen.add(key)
    return result


def _guess_category(name: str) -> str:
    if "传感" in name:
        return "传感器"
    if "开关" in name or "按键" in name:
        return "开关/按钮"
    if "继电" in name:
        return "继电器"
    if "报警" in name or "警报" in name or "指示" in name or "灯" in name:
        return "仪表/指示灯"
    if "线束" in name or "搭铁" in name or "接地" in name:
        return "线束/搭铁"
    if "电源" in name or "电瓶" in name or "蓄电池" in name or "保险" in name:
        return "保险/电源"
    if CONNECTOR_RE.search(name) or "连接器" in name or "端子" in name:
        return "连接器"
    if TOKEN_RE.search(name):
        return "电气信号/控制"
    return "其他"

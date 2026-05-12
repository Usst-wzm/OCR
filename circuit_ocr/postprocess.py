from __future__ import annotations

import re
from collections import OrderedDict

from .models import ComponentCandidate, repair_mojibake


NON_COMPONENT_PATTERNS = [
    re.compile(r"^\d+(\.\d+)?(\s*[xX×]\s*\d+(\.\d+)?)*$"),
    re.compile(r"^[\d\s.,，:：+\-±×xXΦφØø√/()（）]+$"),
    re.compile(r"^[±]?\d+(\.\d+)?\s*°$"),
    re.compile(r"^[±]?\d+(\.\d+)?\s*(mm|cm|m|kg|hz|v|a|r/min|km/h)$", re.IGNORECASE),
    re.compile(r"^\d+(\.\d+)?\s*[±]\s*\d+(\.\d+)?$"),
    re.compile(r"^\d+\s*[xX×]\s*[ΦφØø]?\s*\d+(\.\d+)?"),
    re.compile(r"^[A-J]$"),
    re.compile(r"^\d+$"),
    re.compile(r"^(设计|校对|审核|标准化|批准|日期|名称|材料)$"),
]

COMPONENT_KEYWORDS = (
    "传感器",
    "传感",
    "开关",
    "继电器",
    "继电",
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
    "ECU",
    "信号",
    "温度",
    "液位",
    "车速",
    "转速",
)

COMPONENT_TOKEN_PATTERN = re.compile(
    r"^(X\d+[:：]\d+|CAN[-_ ]?[HL]|ABS\d*|ASR|ECU|VCU|BCM|EBS|DPF|STOP|AdBlue)$",
    re.IGNORECASE,
)

DESCRIPTIVE_NAME_HINTS = (
    "通过",
    "实现",
    "采用",
    "分为",
    "根据",
    "显示",
    "接收",
    "标识",
    "点亮",
    "不点亮",
    "手动",
    "调节",
    "清零",
    "切换",
    "点按",
    "长按",
    "温度为",
    "电压<",
    "为白色",
    "为黑色",
)


def normalize_name(name: str) -> str:
    normalized = repair_mojibake(_strip_measurement_fragments(name))
    normalized = re.sub(r"\s+", "", normalized.strip())
    normalized = normalized.replace("：", ":").replace("（", "(").replace("）", ")")
    normalized = normalized.replace("－", "-").replace("—", "-").replace("–", "-")
    return normalized


def looks_like_component(name: str) -> bool:
    normalized = normalize_name(name)
    if len(normalized) < 2:
        return False
    if COMPONENT_TOKEN_PATTERN.search(normalized):
        return True
    if any(keyword.lower() in normalized.lower() for keyword in COMPONENT_KEYWORDS):
        return True
    if any(pattern.search(normalized) for pattern in NON_COMPONENT_PATTERNS):
        return False
    return False


def exportable_component_name(name: str, category: str = "", confidence: float = 0.0) -> bool:
    normalized = normalize_name(name)
    if not looks_like_component(normalized):
        return False
    if confidence and confidence < 0.8:
        return False
    if any(char in normalized for char in "，,；;。"):
        return False
    if any(hint in normalized for hint in DESCRIPTIVE_NAME_HINTS):
        return False
    if re.search(r"传感器\d+脉冲", normalized):
        return False
    if normalized.endswith("功能"):
        return False
    if len(normalized) > 18 and not COMPONENT_TOKEN_PATTERN.search(normalized):
        return False
    if category == "其他" and not COMPONENT_TOKEN_PATTERN.search(normalized) and len(normalized) > 8:
        return False
    return True


def dedupe_candidates(candidates: list[ComponentCandidate]) -> list[ComponentCandidate]:
    merged: "OrderedDict[tuple[int, str], ComponentCandidate]" = OrderedDict()
    for candidate in candidates:
        key_name = normalize_name(candidate.component_name)
        if not looks_like_component(key_name):
            continue
        key = (candidate.page, key_name.lower())
        existing = merged.get(key)
        candidate.component_name = key_name
        if existing is None:
            merged[key] = candidate
            continue
        existing.confidence = max(existing.confidence, candidate.confidence)
        existing.source_tile = _join_unique(existing.source_tile, candidate.source_tile)
        existing.bbox_or_region = _join_unique(existing.bbox_or_region, candidate.bbox_or_region)
        existing.raw_text = _prefer_longer(existing.raw_text, candidate.raw_text)
        existing.reason = _join_unique(existing.reason, candidate.reason, sep="; ")
    return list(merged.values())


def _join_unique(left: str, right: str, sep: str = ",") -> str:
    values = [part.strip() for part in (left or "").split(sep) if part.strip()]
    for part in (right or "").split(sep):
        cleaned = part.strip()
        if cleaned and cleaned not in values:
            values.append(cleaned)
    return sep.join(values)


def _prefer_longer(left: str, right: str) -> str:
    return right if len(right or "") > len(left or "") else left


def _strip_measurement_fragments(name: str) -> str:
    parts = [part.strip() for part in re.split(r"[,，;；、]", name) if part.strip()]
    if len(parts) <= 1:
        return name
    useful = [part for part in parts if not any(pattern.search(part) for pattern in NON_COMPONENT_PATTERNS)]
    return "、".join(useful) if useful else name

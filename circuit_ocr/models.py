from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Tile:
    page: int
    row: int
    col: int
    x: int
    y: int
    width: int
    height: int
    path: str

    @property
    def tile_id(self) -> str:
        return f"page_{self.page:03d}_r{self.row:02d}_c{self.col:02d}"

    @property
    def region(self) -> str:
        return f"x={self.x},y={self.y},w={self.width},h={self.height}"


@dataclass
class ComponentCandidate:
    page: int
    component_name: str
    category: str = "其他"
    raw_text: str = ""
    bbox_or_region: str = ""
    source_tile: str = ""
    confidence: float = 0.0
    reason: str = ""

    @classmethod
    def from_mapping(
        cls,
        data: dict[str, Any],
        *,
        page: int,
        source_tile: str = "",
        fallback_region: str = "",
    ) -> "ComponentCandidate | None":
        name = repair_mojibake(str(data.get("component_name") or data.get("name") or "").strip())
        if not name:
            return None
        confidence = data.get("confidence", 0.0)
        try:
            confidence_value = float(confidence)
        except (TypeError, ValueError):
            confidence_value = 0.0
        confidence_value = max(0.0, min(1.0, confidence_value))
        bbox = data.get("bbox_or_region") or data.get("bbox") or data.get("region") or fallback_region
        return cls(
            page=_coerce_page(data.get("page"), page),
            component_name=name,
            category=repair_mojibake(str(data.get("category") or "其他").strip()) or "其他",
            raw_text=repair_mojibake(str(data.get("raw_text") or name).strip()),
            bbox_or_region=str(bbox or "").strip(),
            source_tile=str(data.get("source_tile") or source_tile).strip(),
            confidence=confidence_value,
            reason=repair_mojibake(str(data.get("reason") or "").strip()),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _coerce_page(value: Any, fallback: int) -> int:
    try:
        return int(value or fallback)
    except (TypeError, ValueError):
        return int(fallback)


def repair_mojibake(value: str) -> str:
    replacements = {
        "卤": "±",
        "掳": "°",
        "桅": "Φ",
        "脳": "×",
    }
    repaired = value
    for old, new in replacements.items():
        repaired = repaired.replace(old, new)

    try:
        candidate = repaired.encode("gbk", errors="ignore").decode("utf-8", errors="ignore")
    except UnicodeError:
        return repaired
    return candidate if _text_score(candidate) > _text_score(repaired) else repaired


def _text_score(value: str) -> int:
    useful_keywords = ("线束", "指示", "传感", "报警", "开关", "继电", "电源", "搭铁")
    cjk = sum(1 for char in value if "\u4e00" <= char <= "\u9fff")
    ascii_letters = sum(1 for char in value if char.isascii() and char.isalpha())
    keyword_bonus = sum(5 for keyword in useful_keywords if keyword in value)
    mojibake_penalty = sum(value.count(token) * 5 for token in ("绾", "挎", "潫", "鎸", "囩", "鐏", "浼", "犳", "劅", "鍣"))
    replacement_penalty = value.count("?") * 3
    return cjk * 3 + ascii_letters + keyword_bonus - mojibake_penalty - replacement_penalty + len(value) // 10

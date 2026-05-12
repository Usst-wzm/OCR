from __future__ import annotations

import csv
import json
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from .models import ComponentCandidate
from .postprocess import exportable_component_name


FIELDS = [
    "page",
    "component_name",
    "category",
    "raw_text",
    "bbox_or_region",
    "source_tile",
    "confidence",
    "reason",
]

OCR_FIELDS = [
    "page",
    "raw_text",
    "bbox_or_region",
    "source_tile",
    "confidence",
]


def write_json(candidates: list[ComponentCandidate], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([item.to_dict() for item in candidates], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_csv(candidates: list[ComponentCandidate], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        for item in candidates:
            writer.writerow(item.to_dict())


def write_ocr_texts(items: list[ComponentCandidate], json_path: Path, csv_path: Path, txt_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "page": item.page,
            "raw_text": item.raw_text or item.component_name,
            "bbox_or_region": item.bbox_or_region,
            "source_tile": item.source_tile,
            "confidence": item.confidence,
        }
        for item in items
        if item.raw_text or item.component_name
    ]
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=OCR_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    with txt_path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(f"[page {row['page']}] {row['raw_text']}\n")


def write_name_list(candidates: list[ComponentCandidate], txt_path: Path, csv_path: Path) -> None:
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    names = sorted({item.component_name for item in candidates if item.component_name})
    txt_path.write_text("\n".join(names) + ("\n" if names else ""), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["component_name"])
        for name in names:
            writer.writerow([name])


def write_clean_name_list(candidates: list[ComponentCandidate], txt_path: Path, csv_path: Path) -> None:
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    names = sorted(
        {
            item.component_name
            for item in candidates
            if item.component_name and exportable_component_name(item.component_name, item.category, item.confidence)
        }
    )
    txt_path.write_text("\n".join(names) + ("\n" if names else ""), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["component_name"])
        for name in names:
            writer.writerow([name])


def write_xlsx(candidates: list[ComponentCandidate], path: Path) -> bool:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font
    except ImportError:
        _write_minimal_xlsx(candidates, path)
        return True

    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "components"
    sheet.append(FIELDS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for item in candidates:
        sheet.append([item.to_dict().get(field, "") for field in FIELDS])
    widths = {
        "A": 8,
        "B": 32,
        "C": 16,
        "D": 36,
        "E": 34,
        "F": 24,
        "G": 12,
        "H": 48,
    }
    for column, width in widths.items():
        sheet.column_dimensions[column].width = width
    for row in sheet.iter_rows():
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    workbook.save(path)
    return True


def _write_minimal_xlsx(candidates: list[ComponentCandidate], path: Path) -> None:
    """Write a simple XLSX using only the standard library."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [FIELDS] + [[item.to_dict().get(field, "") for field in FIELDS] for item in candidates]
    sheet_rows = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for column_index, value in enumerate(row, start=1):
            ref = f"{_column_name(column_index)}{row_index}"
            text = escape(str(value))
            cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>')
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData>'
        f'{"".join(sheet_rows)}'
        '</sheetData>'
        '</worksheet>'
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '</Types>',
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/>'
            '</Relationships>',
        )
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="components" sheetId="1" r:id="rId1"/></sheets>'
            '</workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            'Target="worksheets/sheet1.xml"/>'
            '</Relationships>',
        )
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)


def _column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name

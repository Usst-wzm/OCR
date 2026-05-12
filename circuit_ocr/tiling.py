from __future__ import annotations

from pathlib import Path

from PIL import Image

from .models import Tile


def iter_tiles(
    page_image: Path,
    page: int,
    output_dir: Path,
    *,
    tile_size: int = 1800,
    overlap: int = 220,
    mode: str = "page",
    page_max_side: int = 4096,
    grid_rows: int = 2,
    grid_cols: int = 3,
    grid_overlap: int = 260,
) -> list[Tile]:
    if overlap >= tile_size:
        raise ValueError("overlap must be smaller than tile_size")

    output_dir.mkdir(parents=True, exist_ok=True)
    tiles: list[Tile] = []
    stride = tile_size - overlap

    with Image.open(page_image) as image:
        width, height = image.size
        if mode == "page":
            tile_path = output_dir / f"page_{page:03d}_full.png"
            if not tile_path.exists():
                page_image_for_ocr = _resize_for_page_mode(image, page_max_side)
                page_image_for_ocr.save(tile_path)
            tiles.append(
                Tile(
                    page=page,
                    row=0,
                    col=0,
                    x=0,
                    y=0,
                    width=width,
                    height=height,
                    path=str(tile_path),
                )
            )
            return tiles
        if mode == "grid":
            return _iter_grid_tiles(
                image=image,
                page=page,
                output_dir=output_dir,
                rows=grid_rows,
                cols=grid_cols,
                overlap=grid_overlap,
            )
        if mode != "tiles":
            raise ValueError(f"unsupported tiling mode: {mode}")
        y_values = _starts(height, tile_size, stride)
        x_values = _starts(width, tile_size, stride)
        for row, y in enumerate(y_values):
            for col, x in enumerate(x_values):
                right = min(x + tile_size, width)
                bottom = min(y + tile_size, height)
                tile_path = output_dir / f"page_{page:03d}_r{row:02d}_c{col:02d}.png"
                if not tile_path.exists():
                    image.crop((x, y, right, bottom)).save(tile_path)
                tiles.append(
                    Tile(
                        page=page,
                        row=row,
                        col=col,
                        x=x,
                        y=y,
                        width=right - x,
                        height=bottom - y,
                        path=str(tile_path),
                    )
                )
    return tiles


def _starts(length: int, tile_size: int, stride: int) -> list[int]:
    if length <= tile_size:
        return [0]
    values = list(range(0, max(1, length - tile_size + 1), stride))
    last = length - tile_size
    if values[-1] != last:
        values.append(last)
    return values


def _resize_for_page_mode(image: Image.Image, max_side: int) -> Image.Image:
    if max_side <= 0:
        return image.copy()
    width, height = image.size
    current_max = max(width, height)
    if current_max <= max_side:
        return image.copy()
    scale = max_side / current_max
    new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
    return image.resize(new_size, Image.Resampling.LANCZOS)


def _iter_grid_tiles(
    *,
    image: Image.Image,
    page: int,
    output_dir: Path,
    rows: int,
    cols: int,
    overlap: int,
) -> list[Tile]:
    if rows <= 0 or cols <= 0:
        raise ValueError("grid rows and cols must be positive")
    width, height = image.size
    cell_width = width / cols
    cell_height = height / rows
    tiles: list[Tile] = []
    for row in range(rows):
        for col in range(cols):
            left = max(0, int(col * cell_width) - overlap)
            top = max(0, int(row * cell_height) - overlap)
            right = min(width, int((col + 1) * cell_width) + overlap)
            bottom = min(height, int((row + 1) * cell_height) + overlap)
            tile_path = output_dir / f"page_{page:03d}_g{row:02d}_{col:02d}.png"
            if not tile_path.exists():
                image.crop((left, top, right, bottom)).save(tile_path)
            tiles.append(
                Tile(
                    page=page,
                    row=row,
                    col=col,
                    x=left,
                    y=top,
                    width=right - left,
                    height=bottom - top,
                    path=str(tile_path),
                )
            )
    return tiles

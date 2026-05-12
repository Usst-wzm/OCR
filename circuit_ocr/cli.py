from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys
from pathlib import Path

from .aliyun_ocr import AliyunOCRExtractor
from .component_matcher import extract_components_from_ocr, load_terms
from .exporters import write_clean_name_list, write_csv, write_json, write_name_list, write_ocr_texts, write_xlsx
from .llm import VisionExtractor
from .pdf_render import get_pdf_page_count, render_page
from .postprocess import dedupe_candidates
from .tiling import iter_tiles


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract component names from scanned circuit drawing PDFs.")
    parser.add_argument("--pdf", required=True, help="Path to the scanned PDF.")
    parser.add_argument("--out", default="outputs", help="Output directory.")
    parser.add_argument("--dpi", type=int, default=240, help="PDF render DPI.")
    parser.add_argument("--tile-size", type=int, default=1800, help="Tile size in pixels.")
    parser.add_argument("--overlap", type=int, default=220, help="Tile overlap in pixels.")
    parser.add_argument(
        "--mode",
        choices=["page", "grid", "tiles"],
        default="grid",
        help="OCR mode. grid uses a few large page regions; page uses one compressed image; tiles uses many crops.",
    )
    parser.add_argument("--page-max-side", type=int, default=4096, help="Max image side for page mode.")
    parser.add_argument("--grid-rows", type=int, default=2, help="Rows for grid mode.")
    parser.add_argument("--grid-cols", type=int, default=3, help="Columns for grid mode.")
    parser.add_argument("--grid-overlap", type=int, default=260, help="Pixel overlap for grid mode.")
    parser.add_argument("--ocr-workers", type=int, default=1, help="Parallel OCR requests. Use 4-8 for cloud OCR.")
    parser.add_argument("--page-review", action="store_true", help="Use an extra model call per page to merge candidates.")
    parser.add_argument("--component-terms", default="component_terms.txt", help="Component keyword dictionary file.")
    parser.add_argument("--pages", help="Page selection, e.g. 1,3-5. Defaults to all pages.")
    parser.add_argument(
        "--ocr-provider",
        choices=["model", "aliyun"],
        default="model",
        help="OCR provider. model uses an OpenAI-compatible vision model; aliyun uses Alibaba Cloud OCR.",
    )
    parser.add_argument("--model", help="OpenAI compatible model name. Defaults to OPENAI_MODEL.")
    parser.add_argument("--base-url", help="OpenAI compatible base URL. Defaults to OPENAI_BASE_URL.")
    parser.add_argument("--api-key", help="API key. Defaults to OPENAI_API_KEY.")
    parser.add_argument("--aliyun-access-key-id", help="Alibaba Cloud AccessKey ID. Defaults to ALIBABA_CLOUD_ACCESS_KEY_ID.")
    parser.add_argument(
        "--aliyun-access-key-secret",
        help="Alibaba Cloud AccessKey secret. Defaults to ALIBABA_CLOUD_ACCESS_KEY_SECRET.",
    )
    parser.add_argument(
        "--aliyun-security-token",
        help="Alibaba Cloud STS security token. Defaults to ALIBABA_CLOUD_SECURITY_TOKEN.",
    )
    parser.add_argument(
        "--aliyun-endpoint",
        help="Alibaba Cloud OCR endpoint. Defaults to ALIYUN_OCR_ENDPOINT or ocr-api.cn-hangzhou.aliyuncs.com.",
    )
    parser.add_argument("--pdftoppm-bin", default="pdftoppm", help="pdftoppm executable.")
    parser.add_argument("--pdfinfo-bin", default="pdfinfo", help="pdfinfo executable.")
    parser.add_argument("--keep-debug-tiles", action="store_true", help="Keep rendered tile images under outputs/debug.")
    parser.add_argument("--render-only", action="store_true", help="Render and tile pages without calling the model.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    pdf_path = Path(args.pdf).expanduser().resolve()
    out_dir = Path(args.out).expanduser().resolve()
    debug_dir = out_dir / "debug"
    render_dir = debug_dir / "rendered_pages"
    cache_dir = out_dir / ".cache"

    if not pdf_path.exists():
        print(f"PDF not found: {pdf_path}", file=sys.stderr)
        return 2
    if not pdf_path.is_file():
        print(f"PDF path is not a file: {pdf_path}", file=sys.stderr)
        return 2

    page_count = get_pdf_page_count(pdf_path, args.pdfinfo_bin)
    pages = parse_pages(args.pages, page_count)
    print(f"PDF pages: {page_count}; selected: {','.join(map(str, pages))}")

    extractor = None
    if not args.render_only:
        if args.ocr_provider == "aliyun":
            extractor = AliyunOCRExtractor(
                access_key_id=args.aliyun_access_key_id,
                access_key_secret=args.aliyun_access_key_secret,
                endpoint=args.aliyun_endpoint,
                security_token=args.aliyun_security_token,
            )
        else:
            extractor = VisionExtractor(model=args.model, api_key=args.api_key, base_url=args.base_url)

    terms_path = Path(args.component_terms).expanduser()
    if not terms_path.is_absolute():
        terms_path = Path.cwd() / terms_path
    terms = load_terms(terms_path if terms_path.exists() else None)

    all_ocr_texts = []
    all_candidates = []
    for page in pages:
        print(f"[page {page}] rendering at {args.dpi} DPI")
        page_image = render_page(
            pdf_path,
            page,
            render_dir,
            dpi=args.dpi,
            pdftoppm_bin=args.pdftoppm_bin,
        )
        tile_dir = debug_dir / f"page_{page:03d}_tiles"
        tiles = iter_tiles(
            page_image,
            page,
            tile_dir,
            tile_size=args.tile_size,
            overlap=args.overlap,
            mode=args.mode,
            page_max_side=args.page_max_side,
            grid_rows=args.grid_rows,
            grid_cols=args.grid_cols,
            grid_overlap=args.grid_overlap,
        )
        print(f"[page {page}] OCR images: {len(tiles)} ({args.mode} mode)")
        if args.render_only:
            continue

        page_ocr_texts = []
        assert extractor is not None
        if args.ocr_workers <= 1 or len(tiles) <= 1:
            for index, tile in enumerate(tiles, start=1):
                print(f"[page {page}] OCR tile {index}/{len(tiles)} {tile.tile_id}")
                cache_path = cache_dir / f"{args.ocr_provider}_tiles_v2" / f"{tile.tile_id}.json"
                page_ocr_texts.extend(extractor.extract_tile(tile, cache_path))
        else:
            max_workers = min(args.ocr_workers, len(tiles))
            print(f"[page {page}] OCR parallel workers: {max_workers}")
            indexed_results = {}
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {
                    pool.submit(
                        extractor.extract_tile,
                        tile,
                        cache_dir / f"{args.ocr_provider}_tiles_v2" / f"{tile.tile_id}.json",
                    ): (index, tile)
                    for index, tile in enumerate(tiles, start=1)
                }
                for done_count, future in enumerate(as_completed(futures), start=1):
                    index, tile = futures[future]
                    print(f"[page {page}] OCR tile done {done_count}/{len(tiles)} {tile.tile_id}")
                    indexed_results[index] = future.result()
            for index in sorted(indexed_results):
                page_ocr_texts.extend(indexed_results[index])

        all_ocr_texts.extend(page_ocr_texts)
        page_candidates = extract_components_from_ocr(page_ocr_texts, terms=terms)
        print(f"[page {page}] OCR texts: {len(page_ocr_texts)}; matched candidates: {len(page_candidates)}")
        reviewed = (
            extractor.review_page(page, page_candidates, cache_dir / f"{args.ocr_provider}_pages_v2" / f"page_{page:03d}.json")
            if args.page_review
            else page_candidates
        )
        final_page_candidates = dedupe_candidates(reviewed or page_candidates)
        print(f"[page {page}] final candidates: {len(final_page_candidates)}")
        all_candidates.extend(final_page_candidates)

    if args.render_only:
        print(f"Rendered debug files under: {debug_dir}")
        return 0

    final_candidates = sorted(dedupe_candidates(all_candidates), key=lambda item: (item.page, item.component_name))
    write_ocr_texts(
        all_ocr_texts,
        out_dir / "all_ocr_texts.json",
        out_dir / "all_ocr_texts.csv",
        out_dir / "all_ocr_texts.txt",
    )
    write_json(final_candidates, out_dir / "components.json")
    write_csv(final_candidates, out_dir / "components.csv")
    write_name_list(final_candidates, out_dir / "component_names.txt", out_dir / "component_names.csv")
    write_clean_name_list(
        final_candidates,
        out_dir / "component_names_clean.txt",
        out_dir / "component_names_clean.csv",
    )
    xlsx_written = write_xlsx(final_candidates, out_dir / "components.xlsx")
    print(f"Done. Components: {len(final_candidates)}")
    print(f"JSON: {out_dir / 'components.json'}")
    print(f"CSV: {out_dir / 'components.csv'}")
    print(f"Names: {out_dir / 'component_names.txt'}")
    print(f"Clean names: {out_dir / 'component_names_clean.txt'}")
    print(f"OCR texts: {out_dir / 'all_ocr_texts.txt'}")
    if xlsx_written:
        print(f"Excel: {out_dir / 'components.xlsx'}")
    if not args.keep_debug_tiles:
        print(f"Debug tiles kept for cache traceability: {debug_dir}")
    return 0


def parse_pages(value: str | None, page_count: int) -> list[int]:
    if not value:
        return list(range(1, page_count + 1))
    pages: set[int] = set()
    for part in value.split(","):
        chunk = part.strip()
        if not chunk:
            continue
        if "-" in chunk:
            start_text, end_text = chunk.split("-", 1)
            start, end = int(start_text), int(end_text)
            if start > end:
                raise ValueError(f"invalid page range: {chunk}")
            pages.update(range(start, end + 1))
        else:
            pages.add(int(chunk))
    invalid = [page for page in pages if page < 1 or page > page_count]
    if invalid:
        raise ValueError(f"pages out of range 1-{page_count}: {invalid}")
    return sorted(pages)


if __name__ == "__main__":
    raise SystemExit(main())

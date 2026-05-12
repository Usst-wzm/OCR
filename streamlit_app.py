from __future__ import annotations

import io
import json
import os
import tempfile
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import streamlit as st

from circuit_ocr.aliyun_ocr import AliyunOCRExtractor
from circuit_ocr.component_matcher import extract_components_from_ocr, load_terms
from circuit_ocr.exporters import (
    write_clean_name_list,
    write_csv,
    write_json,
    write_name_list,
    write_ocr_texts,
    write_xlsx,
)
from circuit_ocr.llm_components import TextComponentExtractor, compact_text_count_for_model
from circuit_ocr.pdf_render import get_pdf_page_count, render_page
from circuit_ocr.postprocess import dedupe_candidates
from circuit_ocr.tiling import iter_tiles


CONFIG_PATH = Path("local_config.json")

st.set_page_config(page_title="电路图 OCR 元器件识别", layout="wide")


def main() -> None:
    config = load_config()
    st.title("电路图 OCR 元器件识别")

    with st.sidebar:
        st.header("OCR 设置")
        mode = st.selectbox(
            "切图模式",
            ["grid", "tiles", "page"],
            index=option_index(["grid", "tiles", "page"], config.get("mode", "grid")),
        )
        pages_text = st.text_input("页码", value=str(config.get("pages", "1")), help="例如 1 或 1,3-5；留空表示全部页")
        dpi = st.number_input("渲染 DPI", min_value=120, max_value=400, value=int(config.get("dpi", 240)), step=20)
        workers = st.slider("OCR 并发数", min_value=1, max_value=12, value=int(config.get("ocr_workers", 6)))

        st.header("阿里云 OCR")
        aliyun_key_id = st.text_input(
            "AccessKey ID",
            value=config.get("aliyun_access_key_id") or os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID", ""),
        )
        aliyun_key_secret = st.text_input(
            "AccessKey Secret",
            value=config.get("aliyun_access_key_secret") or os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", ""),
            type="password",
        )
        aliyun_endpoint = st.text_input(
            "Endpoint",
            value=config.get("aliyun_endpoint") or os.getenv("ALIYUN_OCR_ENDPOINT", "ocr-api.cn-hangzhou.aliyuncs.com"),
        )

        st.header("大模型分类")
        use_llm = st.checkbox(
            "用大模型清洗本地候选名称",
            value=bool(config.get("use_llm", False)),
            help='勾选后，只把本地规则筛出的候选名称用 | 拼成一串发给大模型，只要求返回 {"names":"name1,name2"}。',
        )
        llm_model = st.text_input("模型名", value=config.get("llm_model") or os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
        llm_base_url = st.text_input("Base URL", value=config.get("llm_base_url") or os.getenv("OPENAI_BASE_URL", ""))
        llm_api_key = st.text_input("API Key", value=config.get("llm_api_key") or os.getenv("OPENAI_API_KEY", ""), type="password")

        if st.button("保存本地配置"):
            save_config(
                {
                    "mode": mode,
                    "pages": pages_text,
                    "dpi": int(dpi),
                    "ocr_workers": int(workers),
                    "aliyun_access_key_id": aliyun_key_id,
                    "aliyun_access_key_secret": aliyun_key_secret,
                    "aliyun_endpoint": aliyun_endpoint,
                    "use_llm": use_llm,
                    "llm_model": llm_model,
                    "llm_base_url": llm_base_url,
                    "llm_api_key": llm_api_key,
                }
            )
            st.success(f"已保存到 {CONFIG_PATH}")

    uploaded = st.file_uploader("上传 PDF", type=["pdf"])
    run = st.button("开始识别", type="primary", disabled=uploaded is None)

    if not run:
        st.info("建议先用 grid 模式跑第 1 页。运行时会显示渲染、切图、OCR、本地筛选、大模型分类、导出的完整耗时。")
        return
    if uploaded is None:
        return
    if not aliyun_key_id or not aliyun_key_secret:
        st.error("请填写阿里云 AccessKey ID 和 AccessKey Secret。")
        return
    if use_llm and not llm_api_key:
        st.error("启用大模型分类时，需要填写模型 API Key。")
        return

    with tempfile.TemporaryDirectory(prefix="circuit_ocr_streamlit_") as temp_dir:
        work_dir = Path(temp_dir)
        pdf_path = work_dir / uploaded.name
        pdf_path.write_bytes(uploaded.getvalue())
        out_dir = work_dir / "outputs"

        try:
            result = run_pipeline(
                pdf_path=pdf_path,
                out_dir=out_dir,
                pages_text=pages_text,
                mode=mode,
                dpi=int(dpi),
                workers=int(workers),
                aliyun_key_id=aliyun_key_id,
                aliyun_key_secret=aliyun_key_secret,
                aliyun_endpoint=aliyun_endpoint,
                use_llm=use_llm,
                llm_model=llm_model,
                llm_api_key=llm_api_key,
                llm_base_url=llm_base_url or None,
            )
        except Exception as exc:  # noqa: BLE001 - keep UI failure readable
            st.error(f"识别失败：{exc}")
            return

        show_results(result["candidates"], out_dir)


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        st.warning(f"{CONFIG_PATH} 不是有效 JSON，已忽略。")
        return {}


def save_config(config: dict[str, Any]) -> None:
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def option_index(options: list[str], value: str) -> int:
    return options.index(value) if value in options else 0


def run_pipeline(
    *,
    pdf_path: Path,
    out_dir: Path,
    pages_text: str,
    mode: str,
    dpi: int,
    workers: int,
    aliyun_key_id: str,
    aliyun_key_secret: str,
    aliyun_endpoint: str,
    use_llm: bool,
    llm_model: str,
    llm_api_key: str,
    llm_base_url: str | None,
) -> dict[str, Any]:
    total_started = time.perf_counter()
    debug_dir = out_dir / "debug"
    render_dir = debug_dir / "rendered_pages"
    cache_dir = out_dir / ".cache"

    log_lines: list[str] = []
    log_box = st.empty()
    status = st.empty()
    progress = st.progress(0)
    metrics_box = st.empty()
    metrics: dict[str, float] = {}

    step_started = time.perf_counter()
    page_count = get_pdf_page_count(pdf_path)
    pages = parse_pages(pages_text or None, page_count)
    metrics["读取 PDF 页数"] = elapsed(step_started)
    st.write(f"PDF 共 {page_count} 页，本次识别：{','.join(map(str, pages))}")
    append_log(log_lines, log_box, f"读取 PDF 页数完成：{metrics['读取 PDF 页数']:.2f}s")

    step_started = time.perf_counter()
    extractor = AliyunOCRExtractor(
        access_key_id=aliyun_key_id,
        access_key_secret=aliyun_key_secret,
        endpoint=aliyun_endpoint,
    )
    metrics["初始化 OCR 客户端"] = elapsed(step_started)
    append_log(log_lines, log_box, f"初始化阿里云 OCR 客户端完成：{metrics['初始化 OCR 客户端']:.2f}s")

    all_ocr_texts = []
    total_tiles_done = 0
    total_tiles = 0

    for page in pages:
        page_started = time.perf_counter()
        status.write(f"正在渲染第 {page} 页")
        append_log(log_lines, log_box, f"正在渲染第 {page} 页")

        step_started = time.perf_counter()
        page_image = render_page(pdf_path, page, render_dir, dpi=dpi)
        render_seconds = elapsed(step_started)
        metrics[f"第 {page} 页渲染"] = render_seconds
        append_log(log_lines, log_box, f"第 {page} 页渲染完成：{render_seconds:.2f}s")

        step_started = time.perf_counter()
        tiles = iter_tiles(
            page_image,
            page,
            debug_dir / f"page_{page:03d}_tiles",
            mode=mode,
            tile_size=1800,
            overlap=220,
            grid_rows=2,
            grid_cols=3,
            grid_overlap=260,
            page_max_side=4096,
        )
        tile_seconds = elapsed(step_started)
        metrics[f"第 {page} 页切图"] = tile_seconds
        total_tiles += len(tiles)
        append_log(log_lines, log_box, f"第 {page} 页切图完成：{len(tiles)} 张，{tile_seconds:.2f}s")

        step_started = time.perf_counter()
        append_log(log_lines, log_box, f"第 {page} 页开始 OCR：并发 {min(workers, len(tiles))}")
        page_items = run_tiles_parallel(extractor, tiles, cache_dir, workers, status, log_lines, log_box)
        ocr_seconds = elapsed(step_started)
        metrics[f"第 {page} 页 OCR"] = ocr_seconds
        all_ocr_texts.extend(page_items)
        total_tiles_done += len(tiles)
        progress.progress(min(0.65, 0.65 * total_tiles_done / max(total_tiles, 1)))
        append_log(log_lines, log_box, f"第 {page} 页 OCR 完成：{len(page_items)} 条文本，{ocr_seconds:.2f}s")
        append_log(log_lines, log_box, f"第 {page} 页累计耗时：{elapsed(page_started):.2f}s")
        show_metrics(metrics_box, metrics)

    step_started = time.perf_counter()
    write_ocr_texts(
        all_ocr_texts,
        out_dir / "all_ocr_texts.json",
        out_dir / "all_ocr_texts.csv",
        out_dir / "all_ocr_texts.txt",
    )
    metrics["写出 OCR 文本"] = elapsed(step_started)
    append_log(log_lines, log_box, f"写出 OCR 全量文本完成：{metrics['写出 OCR 文本']:.2f}s")

    step_started = time.perf_counter()
    terms = load_terms(Path("component_terms.txt") if Path("component_terms.txt").exists() else None)
    append_log(log_lines, log_box, "正在用本地规则从 OCR 文本中挑选元器件候选名称")
    candidates = extract_components_from_ocr(all_ocr_texts, terms=terms)
    local_candidates = candidates
    metrics["本地规则筛选"] = elapsed(step_started)
    append_log(log_lines, log_box, f"本地规则抽出候选 {len(candidates)} 个：{metrics['本地规则筛选']:.2f}s")
    append_category_log(log_lines, log_box, candidates, "本地规则候选分类")
    progress.progress(0.75)
    show_metrics(metrics_box, metrics)

    if use_llm and all_ocr_texts:
        step_started = time.perf_counter()
        status.write("正在用大模型清洗本地候选名称")
        compact_count = compact_text_count_for_model(candidates)
        append_log(log_lines, log_box, f"大模型轻量清洗开始：{len(pages)} 页，发送本地候选名称 {compact_count} 条")
        append_log(log_lines, log_box, '要求模型只返回 JSON：{"names":"name1,name2,name3"}')
        llm = TextComponentExtractor(
            model=llm_model,
            api_key=llm_api_key,
            base_url=llm_base_url,
            max_retries=0,
            request_timeout=45.0,
        )
        llm_candidates = llm.clean_names_by_page(
            candidates,
            cache_dir=cache_dir / "llm_clean_candidates_v1",
            progress_callback=lambda message: append_log(log_lines, log_box, message),
        )
        if llm_candidates:
            candidates = llm_candidates
        else:
            candidates = local_candidates
            append_log(log_lines, log_box, "大模型没有返回有效名称，已保留本地规则候选结果")
        metrics["大模型清洗候选"] = elapsed(step_started)
        append_log(log_lines, log_box, f"大模型清洗候选完成：输出 {len(candidates)} 个，{metrics['大模型清洗候选']:.2f}s")
        progress.progress(0.9)
        show_metrics(metrics_box, metrics)

    step_started = time.perf_counter()
    final_candidates = sorted(dedupe_candidates(candidates), key=lambda item: (item.page, item.component_name))
    write_json(final_candidates, out_dir / "components.json")
    write_csv(final_candidates, out_dir / "components.csv")
    write_xlsx(final_candidates, out_dir / "components.xlsx")
    write_name_list(final_candidates, out_dir / "component_names.txt", out_dir / "component_names.csv")
    write_clean_name_list(final_candidates, out_dir / "component_names_clean.txt", out_dir / "component_names_clean.csv")
    metrics["导出结果文件"] = elapsed(step_started)
    append_log(log_lines, log_box, f"导出 JSON/CSV/Excel/TXT 完成：{metrics['导出结果文件']:.2f}s")

    metrics["总耗时"] = elapsed(total_started)
    append_log(log_lines, log_box, f"识别完成，最终元器件名称 {len(final_candidates)} 个")
    append_log(log_lines, log_box, f"总耗时：{metrics['总耗时']:.2f}s")
    show_metrics(metrics_box, metrics)
    status.write("识别完成")
    progress.progress(1.0)
    return {"candidates": final_candidates}


def run_tiles_parallel(
    extractor: AliyunOCRExtractor,
    tiles: list,
    cache_dir: Path,
    workers: int,
    status,
    log_lines: list[str],
    log_box,
) -> list:
    if workers <= 1 or len(tiles) <= 1:
        results = []
        for index, tile in enumerate(tiles, start=1):
            status.write(f"OCR {index}/{len(tiles)}：{tile.tile_id}")
            started = time.perf_counter()
            items = extractor.extract_tile(tile, cache_dir / "aliyun_tiles_v2" / f"{tile.tile_id}.json")
            append_log(log_lines, log_box, f"OCR {index}/{len(tiles)} {tile.tile_id}：{len(items)} 条文本，{elapsed(started):.2f}s")
            results.extend(items)
        return results

    indexed_results = {}
    max_workers = min(workers, len(tiles))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(timed_extract_tile, extractor, tile, cache_dir / "aliyun_tiles_v2" / f"{tile.tile_id}.json"): (index, tile)
            for index, tile in enumerate(tiles, start=1)
        }
        for done_count, future in enumerate(as_completed(futures), start=1):
            index, tile = futures[future]
            status.write(f"OCR 完成 {done_count}/{len(tiles)}：{tile.tile_id}")
            items, seconds = future.result()
            indexed_results[index] = items
            append_log(log_lines, log_box, f"OCR 完成 {done_count}/{len(tiles)} {tile.tile_id}：{len(items)} 条文本，{seconds:.2f}s")

    results = []
    for index in sorted(indexed_results):
        results.extend(indexed_results[index])
    return results


def timed_extract_tile(extractor: AliyunOCRExtractor, tile, cache_path: Path) -> tuple[list, float]:
    started = time.perf_counter()
    return extractor.extract_tile(tile, cache_path), elapsed(started)


def append_log(lines: list[str], log_box, message: str) -> None:
    lines.append(message)
    log_box.code("\n".join(lines[-120:]), language="text")


def append_category_log(lines: list[str], log_box, candidates: list, title: str) -> None:
    counts: dict[str, int] = {}
    for item in candidates:
        counts[item.category] = counts.get(item.category, 0) + 1
    if not counts:
        append_log(lines, log_box, f"{title}：无")
        return
    summary = "，".join(f"{category} {count}" for category, count in sorted(counts.items()))
    append_log(lines, log_box, f"{title}：{summary}")


def show_metrics(metrics_box, metrics: dict[str, float]) -> None:
    if not metrics:
        return
    metrics_box.subheader("耗时明细")
    metrics_box.dataframe(
        [{"步骤": name, "耗时(秒)": round(seconds, 2)} for name, seconds in metrics.items()],
        use_container_width=True,
        hide_index=True,
    )


def elapsed(started_at: float) -> float:
    return time.perf_counter() - started_at


def show_results(candidates: list, out_dir: Path) -> None:
    st.subheader(f"元器件名称结果：{len(candidates)} 个")
    if not candidates:
        st.warning("没有识别出元器件名称。可以尝试 tiles 模式，或检查 OCR 文本是否正常。")
    for index, item in enumerate(candidates, start=1):
        with st.container(border=True):
            cols = st.columns([0.08, 0.36, 0.18, 0.38])
            cols[0].write(index)
            cols[1].markdown(f"**{item.component_name}**")
            cols[2].write(item.category)
            cols[3].caption(f"第 {item.page} 页 | {item.source_tile} | {item.confidence:.2f}")
            if item.raw_text and item.raw_text != item.component_name:
                st.caption(f"来源文本：{item.raw_text}")

    st.subheader("下载结果")
    col1, col2, col3, col4 = st.columns(4)
    col1.download_button("components.xlsx", (out_dir / "components.xlsx").read_bytes(), "components.xlsx")
    col2.download_button("components.csv", (out_dir / "components.csv").read_bytes(), "components.csv")
    col3.download_button("名称 TXT", (out_dir / "component_names_clean.txt").read_bytes(), "component_names_clean.txt")
    col4.download_button("全部输出 ZIP", make_zip(out_dir), "ocr_outputs.zip")


def make_zip(out_dir: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in out_dir.rglob("*"):
            if path.is_file() and ".cache" not in path.parts:
                archive.write(path, path.relative_to(out_dir))
    return buffer.getvalue()


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
            pages.update(range(start, end + 1))
        else:
            pages.add(int(chunk))
    invalid = [page for page in pages if page < 1 or page > page_count]
    if invalid:
        raise ValueError(f"页码超出范围 1-{page_count}: {invalid}")
    return sorted(pages)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Download-only large RAG corpus: merge Internet samples until each artifact is >= MIN MiB.

No locally generated prose or synthetic corpora — content originates from HTTP(S) URLs only.
Assembly (concat / PDF merge / OOXML merge of downloaded bytes) is deterministic glue code.

Outputs under rag_data/documents/enterprise_internet/ plus manifest.json with 20 FTS probes
per file derived from extracted text (deterministic “accuracy” for lexical RAG).

Usage::
  export WORKSPACE_ROOT=/path/to/lisa/nvidia_voice
  python3 scripts/rag_enterprise_regression/assemble_internet_corpus.py --min-mib 52
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

_APP_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))


def _download(url: str, dest: Path, *, timeout: int = 180) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120 Safari/537.36",
            "Accept": "*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        dest.write_bytes(resp.read())


# --- URL pools (curated; extend if links rot) ---

ARXIV_IDS = [
    "2303.08774",
    "2312.00752",
    "1706.03762",
    "1810.04805",
    "2005.14165",
    "2103.00020",
    "2204.02311",
    "2211.05100",
    "2106.09685",
    "1907.11692",
    "1910.09701",
    "1803.02155",
    "1808.01214",
    "2010.11929",
    "2102.12092",
    "2104.09864",
    "2108.12409",
    "2112.07472",
    "2201.08239",
    "2203.05525",
    "2207.04672",
    "2210.03629",
    "2301.07069",
    "2302.04045",
    "2305.18290",
    "2307.09288",
    "2309.05463",
    "2310.06825",
    "2311.07911",
    "2401.05566",
]


def _arxiv_pdf_urls() -> list[str]:
    return [f"https://arxiv.org/pdf/{aid}.pdf" for aid in ARXIV_IDS]


GUTENBERG_TXT = [
    "https://www.gutenberg.org/files/1342/1342-0.txt",
    "https://www.gutenberg.org/files/84/84-0.txt",
    "https://www.gutenberg.org/files/11/11-0.txt",
    "https://www.gutenberg.org/files/2701/2701-0.txt",
    "https://www.gutenberg.org/files/74/74-0.txt",
    "https://www.gutenberg.org/files/345/345-0.txt",
    "https://www.gutenberg.org/files/19033/19033-0.txt",
    "https://www.gutenberg.org/files/5200/5200-0.txt",
    "https://www.gutenberg.org/files/1080/1080-0.txt",
    "https://www.gutenberg.org/files/2591/2591-0.txt",
    "https://www.gutenberg.org/files/16328/16328-0.txt",
    "https://www.gutenberg.org/files/4300/4300-0.txt",
]

MARKDOWN_URLS = [
    "https://raw.githubusercontent.com/github/gitignore/main/Python.gitignore",
    "https://raw.githubusercontent.com/github/gitignore/main/Java.gitignore",
    "https://raw.githubusercontent.com/github/gitignore/main/Go.gitignore",
    "https://raw.githubusercontent.com/github/gitignore/main/Rust.gitignore",
    "https://raw.githubusercontent.com/github/gitignore/main/Node.gitignore",
    "https://raw.githubusercontent.com/github/gitignore/main/C++.gitignore",
    "https://raw.githubusercontent.com/github/gitignore/main/Global/macOS.gitignore",
    "https://raw.githubusercontent.com/github/gitignore/main/Global/Linux.gitignore",
    "https://raw.githubusercontent.com/github/gitignore/main/Global/Windows.gitignore",
    "https://raw.githubusercontent.com/github/gitignore/main/Global/JetBrains.gitignore",
]

CSV_URLS = [
    "https://raw.githubusercontent.com/datasets/country-codes/master/data/country-codes.csv",
    "https://raw.githubusercontent.com/datasets/language-codes/master/data/language-codes.csv",
    "https://raw.githubusercontent.com/datasets/airport-codes/master/data/airport-codes.csv",
]

FILESAMPLES_DOCX = [
    "https://filesamples.com/samples/document/docx/sample1.docx",
    "https://filesamples.com/samples/document/docx/sample2.docx",
    "https://filesamples.com/samples/document/docx/sample3.docx",
    "https://filesamples.com/samples/document/docx/sample4.docx",
]

FILESAMPLES_XLSX = [
    "https://filesamples.com/samples/document/xlsx/sample1.xlsx",
    "https://filesamples.com/samples/document/xlsx/sample2.xlsx",
    "https://filesamples.com/samples/document/xlsx/sample3.xlsx",
]

FILESAMPLES_XLS = [
    "https://filesamples.com/samples/document/xls/sample3.xls",
]


def _pptx_urls_from_github_tree() -> list[str]:
    base = "https://raw.githubusercontent.com/scanny/python-pptx/master/"
    # Known-good fixtures (subset of repo); extend via API if needed.
    rel = [
        "features/steps/test_files/minimal.pptx",
        "features/steps/test_files/prs-add-slide.pptx",
        "features/steps/test_files/cht-charts.pptx",
        "features/steps/test_files/cht-chart-type.pptx",
        "features/steps/test_files/cht-series.pptx",
        "features/steps/test_files/dml-fill.pptx",
        "features/steps/test_files/dml-line.pptx",
        "features/steps/test_files/mst-shapes.pptx",
        "features/steps/test_files/mst-slide-layouts.pptx",
        "features/steps/test_files/lyt-shapes.pptx",
        "features/steps/test_files/ph-populated-placeholders.pptx",
        "features/steps/test_files/ph-unpopulated-placeholders.pptx",
        "features/steps/test_files/ext-rels.pptx",
        "features/steps/test_files/font-color.pptx",
        "features/steps/test_files/cht-axis-props.pptx",
        "features/steps/test_files/cht-legend.pptx",
        "features/steps/test_files/cht-marker-props.pptx",
        "features/steps/test_files/cht-gridlines-props.pptx",
        "features/steps/test_files/cht-datalabels.pptx",
        "features/steps/test_files/cht-category-access.pptx",
    ]
    return [base + r for r in rel]


def _derive_probes(text: str, n: int, chunk_text_fn) -> list[str]:
    """Pick substrings that lie entirely inside single RAG chunks (matches rag_fts rows)."""
    chunks = chunk_text_fn(text or "")
    if not chunks:
        return ["empty"] * n
    viable = [j for j, c in enumerate(chunks) if len(re.sub(r"\s+", " ", c.strip())) >= 24]
    if not viable:
        viable = list(range(len(chunks)))
    out: list[str] = []
    for i in range(n):
        slot = int(round(i * (len(viable) - 1) / max(1, n - 1))) if n > 1 else 0
        ci = viable[slot]
        raw_c = chunks[ci]
        c = re.sub(r"\s+", " ", raw_c.strip())
        if len(c) < 12:
            out.append(c[:80] if c else "token")
            continue
        pos = max(0, len(c) // 5)
        span = c[pos : pos + min(160, max(24, len(c) // 2))].strip()
        out.append(span[:220])
    return out


def _grow_pdf(part_files: list[Path], out: Path, min_bytes: int) -> None:
    from pypdf import PdfReader, PdfWriter

    def append_parts(w: PdfWriter) -> None:
        for p in part_files:
            r = PdfReader(str(p))
            for page in r.pages:
                w.add_page(page)

    w = PdfWriter()
    append_parts(w)
    with open(out, "wb") as f:
        w.write(f)
    guard = 0
    while out.stat().st_size < min_bytes:
        guard += 1
        if guard > 40:
            raise RuntimeError("pdf: could not reach target size")
        w = PdfWriter()
        r0 = PdfReader(str(out))
        for page in r0.pages:
            w.add_page(page)
        append_parts(w)
        with open(out, "wb") as f:
            w.write(f)


def _grow_concat_text(paths: list[Path], out: Path, min_bytes: int, *, sep: bytes) -> None:
    parts: list[bytes] = []
    for p in paths:
        parts.append(p.read_bytes())
    blob = sep.join(parts)
    round_n = 0
    while len(blob) < min_bytes:
        blob = blob + sep + blob
        round_n += 1
        if round_n > 28:
            raise RuntimeError("text concat: could not reach target size")
    raw = blob[:min_bytes]
    out.write_bytes(raw)
    if out.stat().st_size < min_bytes:
        raise RuntimeError("text concat: truncation lost target size")


def _grow_docx(part_files: list[Path], out: Path, min_bytes: int) -> None:
    from docx import Document

    def build_once() -> Document:
        master = Document()
        for p in part_files:
            d = Document(str(p))
            for para in d.paragraphs:
                t = (para.text or "").strip()
                if t:
                    master.add_paragraph(t)
        return master

    doc = build_once()
    doc.save(out)
    guard = 0
    while out.stat().st_size < min_bytes:
        guard += 1
        if guard > 400:
            raise RuntimeError("docx: could not reach target size")
        m = Document(str(out))
        extra = build_once()
        for para in extra.paragraphs:
            t = (para.text or "").strip()
            if t:
                # Round tag defeats OOXML zip compression collapse on repeated paragraphs.
                m.add_paragraph(f"c{guard}|{t}"[:12000])
        m.save(out)


def _grow_xlsx(part_files: list[Path], out: Path, min_bytes: int) -> None:
    from openpyxl import Workbook, load_workbook

    def pump_sheet(ws, guard: int) -> None:
        for p in part_files:
            wb_i = load_workbook(p, read_only=True, data_only=True)
            for sheet in wb_i.worksheets:
                for row in sheet.iter_rows(values_only=True):
                    vals = []
                    for v in row:
                        if isinstance(v, str) and v.strip():
                            vals.append(f"x{guard}|{v}"[:32000])
                        else:
                            vals.append("" if v is None else v)
                    ws.append(vals)
            wb_i.close()

    wb_m = Workbook()
    ws = wb_m.active
    ws.title = "internet_merge"
    pump_sheet(ws, 0)
    wb_m.save(out)

    guard = 0
    while out.stat().st_size < min_bytes:
        guard += 1
        if guard > 400:
            raise RuntimeError("xlsx: could not reach target size")
        wb_m = load_workbook(out, read_only=False, data_only=True)
        ws = wb_m[wb_m.sheetnames[0]]
        pump_sheet(ws, guard)
        wb_m.save(out)


def _grow_xls(part_files: list[Path], out: Path, min_bytes: int) -> None:
    import xlrd
    import xlwt

    def pump(ws, start_row: int, guard: int) -> int:
        r = start_row
        for p in part_files:
            book = xlrd.open_workbook(str(p), on_demand=True)
            for sname in book.sheet_names():
                sh = book.sheet_by_name(sname)
                for ri in range(sh.nrows):
                    if r >= 65530:
                        book.release_resources()
                        return r
                    for ci in range(sh.ncols):
                        val = sh.cell_value(ri, ci)
                        if isinstance(val, str):
                            val = f"l{guard}|{val}"[:8000]
                        ws.write(r, ci, val)
                    r += 1
            book.release_resources()
        return r

    wb_w = xlwt.Workbook()
    ws = wb_w.add_sheet("m")
    pump(ws, 0, 0)
    wb_w.save(out)

    guard = 0
    while out.stat().st_size < min_bytes:
        guard += 1
        if guard > 800:
            raise RuntimeError("xls: could not reach target size")
        rb = xlrd.open_workbook(str(out), on_demand=True)
        sheet = rb.sheet_by_index(0)
        wb_w = xlwt.Workbook()
        ws = wb_w.add_sheet("m")
        nr = min(sheet.nrows, 65530)
        for ri in range(nr):
            for ci in range(sheet.ncols):
                ws.write(ri, ci, sheet.cell_value(ri, ci))
        rb.release_resources()
        pump(ws, nr, guard)
        wb_w.save(out)


def _grow_pptx(part_files: list[Path], out: Path, min_bytes: int) -> None:
    from pptx import Presentation
    from pptx.util import Inches, Pt

    def merge_into_master(master: Presentation, tag: int) -> None:
        blank_i = min(6, max(0, len(master.slide_layouts) - 1))
        blank = master.slide_layouts[blank_i]
        for p in part_files:
            src = Presentation(str(p))
            for slide in src.slides:
                dest = master.slides.add_slide(blank)
                box = dest.shapes.add_textbox(Inches(0.3), Inches(0.3), Inches(9.4), Inches(7))
                tf = box.text_frame
                tf.clear()
                lines = []
                for shape in slide.shapes:
                    t = getattr(shape, "text", "") or ""
                    if t.strip():
                        lines.append(t.strip())
                blob = "\n".join(lines) or "(slide)"
                tf.paragraphs[0].text = f"p{tag}|{blob}"[:12000]
                tf.paragraphs[0].font.size = Pt(10)

    master = Presentation()
    merge_into_master(master, 0)
    master.save(out)

    guard = 0
    while out.stat().st_size < min_bytes:
        guard += 1
        if guard > 400:
            raise RuntimeError("pptx: could not reach target size")
        base = Presentation(str(out))
        merge_into_master(base, guard)
        base.save(out)


def _download_pool(urls: list[str], cache: Path, label: str) -> list[Path]:
    cache.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    for i, url in enumerate(urls):
        h = hashlib.sha256(url.encode()).hexdigest()[:12]
        tail = url.rsplit(".", 1)[-1].lower() if "." in url else "dat"
        suf = tail if tail in {"pdf", "txt", "md", "csv", "docx", "xlsx", "xls", "pptx", "markdown"} else "dat"
        dest = cache / f"{label}_{i}_{h}.{suf}"
        print(f"  GET {url}", flush=True)
        _download(url, dest)
        if dest.stat().st_size < 100:
            raise RuntimeError(f"download too small or failed: {url}")
        head = dest.read_bytes()[:8]
        if dest.suffix.lower() == ".pdf" and not head.startswith(b"%PDF"):
            raise RuntimeError(f"not a PDF response: {url}")
        out.append(dest)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--min-mib", type=int, default=52, help="Minimum output size per artifact (default 52 MiB)")
    ap.add_argument(
        "--smoke",
        action="store_true",
        help="Fewer downloads for quick verification (still respects --min-mib unless tiny)",
    )
    args = ap.parse_args()
    min_bytes = max(int(args.min_mib), 1) * 1024 * 1024

    import os

    os.environ.setdefault("LOCAL_RAG_DATA_DIR", "")
    # Probe derivation must use the same extraction breadth as regression ingestion.
    os.environ.setdefault("LOCAL_RAG_PDF_MAX_PAGES", "0")
    os.environ.setdefault("LOCAL_RAG_PPTX_MAX_SLIDES", "0")
    os.environ.setdefault("LOCAL_RAG_XLSX_MAX_SHEETS", "0")

    import importlib

    import pipecat_bots.local_rag as lr_mod

    importlib.reload(lr_mod)

    _extract_text_from_path = lr_mod._extract_text_from_path
    _chunk_text = lr_mod._chunk_text
    rag_documents_dir = lr_mod.rag_documents_dir

    arxiv_urls = _arxiv_pdf_urls()
    gut_urls = GUTENBERG_TXT
    md_urls = MARKDOWN_URLS
    csv_urls = CSV_URLS
    docx_urls = FILESAMPLES_DOCX
    xlsx_urls = FILESAMPLES_XLSX
    xls_urls = FILESAMPLES_XLS * 4
    ppt_urls = _pptx_urls_from_github_tree()
    if args.smoke:
        arxiv_urls = arxiv_urls[:5]
        gut_urls = gut_urls[:4]
        md_urls = md_urls[:4]
        csv_urls = csv_urls[:2]
        docx_urls = FILESAMPLES_DOCX[:4]
        xlsx_urls = xlsx_urls[:3]
        xls_urls = FILESAMPLES_XLS * 2
        ppt_urls = ppt_urls[:8]

    parts_root = rag_documents_dir() / "enterprise_internet_parts"
    out_root = rag_documents_dir() / "enterprise_internet"
    shutil.rmtree(out_root, ignore_errors=True)
    shutil.rmtree(parts_root, ignore_errors=True)
    out_root.mkdir(parents=True, exist_ok=True)
    parts_root.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []

    # PDF
    pdf_cached = _download_pool(arxiv_urls, parts_root / "pdf", "arxiv")
    pdf_out = out_root / "internet_merged.pdf"
    _grow_pdf(pdf_cached, pdf_out, min_bytes)
    tx, err = _extract_text_from_path(pdf_out)
    rows.append(
        {
            "kind": "pdf",
            "filename": pdf_out.name,
            "bytes_on_disk": pdf_out.stat().st_size,
            "source_urls": arxiv_urls,
            "assembly": "pypdf merge / expand until size target",
            "extract_error": err,
            "probes": _derive_probes(tx or "", 20, _chunk_text),
        }
    )

    # TXT
    txt_cached = _download_pool(gut_urls, parts_root / "txt", "gutenberg")
    txt_out = out_root / "internet_merged.txt"
    _grow_concat_text(txt_cached, txt_out, min_bytes, sep=b"\n\n")
    tx, err = _extract_text_from_path(txt_out)
    rows.append(
        {
            "kind": "txt",
            "filename": txt_out.name,
            "bytes_on_disk": txt_out.stat().st_size,
            "source_urls": gut_urls,
            "assembly": "binary concatenation / exponential duplicate until target",
            "extract_error": err,
            "probes": _derive_probes(tx or "", 20, _chunk_text),
        }
    )

    md_cached = _download_pool(md_urls, parts_root / "md", "md")
    md_out = out_root / "internet_merged.md"
    _grow_concat_text(md_cached, md_out, min_bytes, sep=b"\n\n")
    tx, err = _extract_text_from_path(md_out)
    rows.append(
        {
            "kind": "md",
            "filename": md_out.name,
            "bytes_on_disk": md_out.stat().st_size,
            "source_urls": md_urls,
            "assembly": "concatenate downloaded gitignore-style text as markdown corpus",
            "extract_error": err,
            "probes": _derive_probes(tx or "", 20, _chunk_text),
        }
    )

    mk_out = out_root / "internet_merged.markdown"
    # Distinct bytes from .md so content-hash dedupe does not drop this path from FTS.
    mk_out.write_bytes(md_out.read_bytes() + b"\n\n<!-- enterprise-internet-markdown-v1 -->\n")
    tx2, err2 = _extract_text_from_path(mk_out)
    rows.append(
        {
            "kind": "markdown",
            "filename": mk_out.name,
            "bytes_on_disk": mk_out.stat().st_size,
            "source_urls": md_urls,
            "assembly": "same bytes as internet_merged.md (.markdown suffix)",
            "extract_error": err2,
            "probes": _derive_probes(tx2 or "", 20, _chunk_text),
        }
    )

    csv_cached = _download_pool(csv_urls, parts_root / "csv", "csv")
    csv_out = out_root / "internet_merged.csv"
    _grow_concat_text(csv_cached, csv_out, min_bytes, sep=b"\n")
    tx, err = _extract_text_from_path(csv_out)
    rows.append(
        {
            "kind": "csv",
            "filename": csv_out.name,
            "bytes_on_disk": csv_out.stat().st_size,
            "source_urls": csv_urls,
            "assembly": "concatenate CSV bytes until target",
            "extract_error": err,
            "probes": _derive_probes(tx or "", 20, _chunk_text),
        }
    )

    docx_cached = _download_pool(docx_urls, parts_root / "docx", "docx")
    docx_out = out_root / "internet_merged.docx"
    _grow_docx(docx_cached, docx_out, min_bytes)
    tx, err = _extract_text_from_path(docx_out)
    rows.append(
        {
            "kind": "docx",
            "filename": docx_out.name,
            "bytes_on_disk": docx_out.stat().st_size,
            "source_urls": docx_urls,
            "assembly": "merge paragraphs from FileSamples DOCX; duplicate merge until target",
            "extract_error": err,
            "probes": _derive_probes(tx or "", 20, _chunk_text),
        }
    )

    xlsx_cached = _download_pool(xlsx_urls, parts_root / "xlsx", "xlsx")
    xlsx_out = out_root / "internet_merged.xlsx"
    _grow_xlsx(xlsx_cached, xlsx_out, min_bytes)
    tx, err = _extract_text_from_path(xlsx_out)
    rows.append(
        {
            "kind": "xlsx",
            "filename": xlsx_out.name,
            "bytes_on_disk": xlsx_out.stat().st_size,
            "source_urls": xlsx_urls,
            "assembly": "openpyxl merge sheets; append rounds until target",
            "extract_error": err,
            "probes": _derive_probes(tx or "", 20, _chunk_text),
        }
    )

    xls_cached = _download_pool(xls_urls, parts_root / "xls", "xls")
    xls_out = out_root / "internet_merged.xls"
    _grow_xls(xls_cached, xls_out, min_bytes)
    tx, err = _extract_text_from_path(xls_out)
    rows.append(
        {
            "kind": "xls",
            "filename": xls_out.name,
            "bytes_on_disk": xls_out.stat().st_size,
            "source_urls": list(dict.fromkeys(xls_urls)),
            "assembly": "xlrd/xlwt merge; duplicate rounds (same Internet sample repeated)",
            "extract_error": err,
            "probes": _derive_probes(tx or "", 20, _chunk_text),
        }
    )

    ppt_cached = _download_pool(ppt_urls, parts_root / "pptx", "pptx")
    ppt_out = out_root / "internet_merged.pptx"
    _grow_pptx(ppt_cached, ppt_out, min_bytes)
    tx, err = _extract_text_from_path(ppt_out)
    rows.append(
        {
            "kind": "pptx",
            "filename": ppt_out.name,
            "bytes_on_disk": ppt_out.stat().st_size,
            "source_urls": ppt_urls,
            "assembly": "python-pptx fixtures: slide text merged / stacked until target",
            "extract_error": err,
            "probes": _derive_probes(tx or "", 20, _chunk_text),
        }
    )

    for r in rows:
        if int(r["bytes_on_disk"]) < min_bytes:
            raise RuntimeError(f'{r["filename"]}: {r["bytes_on_disk"]} < {min_bytes}')

    lr_mod.init_schema()
    for r in rows:
        fp = out_root / r["filename"]
        ing = lr_mod.ingest_file(fp)
        print(f"Ingest {fp.name}: ok={ing.get('ok')} chunks={ing.get('chunks')}", flush=True)

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "min_bytes": min_bytes,
        "corpus_root": str(out_root.resolve()),
        "definition_of_accuracy": "Each probe substring appears in normalized extracted text and FTS has ≥1 chunk for the output path (lexical pipeline; not LLM answers).",
        "files": rows,
    }
    (out_root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Wrote {out_root / 'manifest.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
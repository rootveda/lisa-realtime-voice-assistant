#!/usr/bin/env python3
"""Generate >=50MiB per supported RAG suffix under rag_data/documents/enterprise_regression/.

Embeds 20 unique deterministic markers per file type for FTS retrieval regression.
Requires: python-docx, openpyxl, xlwt, python-pptx, pypdf, reportlab (see app/requirements.txt).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_APP_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))

# Lazy imports inside writers to fail with clear errors.

TARGET_BYTES_DEFAULT = 52 * 1024 * 1024  # 52 MiB (>= 50 MiB requirement)


def _markers(kind: str) -> list[str]:
    """Twenty tokens engineered for FTS (word chars; unlikely Porter collisions)."""
    k = kind.upper().replace(".", "")
    return [f"ENTREG2026{k}_{i:02d}_QX{i:04d}ZZ99VERIFY" for i in range(1, 21)]


def _ensure_min_size(path: Path, minimum: int) -> None:
    sz = path.stat().st_size if path.is_file() else 0
    if sz < minimum:
        raise RuntimeError(f"{path}: size {sz} < required minimum {minimum}")


def _write_txt_like(path: Path, markers: list[str], target: int, *, sep: str = "\n") -> None:
    """Embed every marker up front, then pad so sparse indexing cannot miss late-case tokens."""
    filler_line = "AlphanumericRegressionPad " * 40 + sep
    filler_b = filler_line.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    head = sep.join(markers) + sep
    head_b = head.encode("utf-8")
    written = len(head_b)
    with path.open("wb") as f:
        f.write(head_b)
        while written < target:
            f.write(filler_b)
            written += len(filler_b)


def _write_csv(path: Path, markers: list[str], target: int) -> None:
    filler_cell = "CsvPad " * 80
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["col_a", "col_b", "col_c"])
        for m in markers:
            w.writerow([m, "marker_row", m[-8:]])
    written = path.stat().st_size
    with path.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        row_n = 0
        while written < target:
            w.writerow([filler_cell, row_n, filler_cell[:120]])
            written += len(filler_cell) + 30
            row_n += 1


def _write_docx(path: Path, markers: list[str], target: int) -> None:
    from docx import Document

    doc = Document()
    doc.add_heading("Lisa RAG regression corpus", level=1)
    for m in markers:
        doc.add_paragraph(m)
    path.parent.mkdir(parents=True, exist_ok=True)
    batches = 0
    while True:
        block_lines = []
        for i in range(160):
            n = batches + i
            salt = hashlib.sha256(str(n).encode()).hexdigest()
            block_lines.append(f"DocxEnterpriseRegressionLine{n:09d}_{salt}")
        doc.add_paragraph("\n".join(block_lines))
        batches += 160
        # Whole-document OOXML rewrites are costly; fewer paragraphs ⇒ fewer saves.
        if batches % 19200 == 0:
            doc.save(path)
            if path.stat().st_size >= target:
                return
        if batches > 20_000_000:
            raise RuntimeError("docx generation exceeded iteration guard")


def _write_xlsx(path: Path, markers: list[str], target: int) -> None:
    from openpyxl import Workbook

    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "enterprise_data"
    ws.append(["col_a", "col_b", "col_c"])
    for i, m in enumerate(markers):
        ws.append([m, "marker", i])
    row = len(markers) + 1
    while True:
        filler = "XlsxPad " + hashlib.sha256(str(row).encode()).hexdigest() + " " * 120
        ws.append([filler, row, filler[:260]])
        row += 1
        if row % 800 == 0:
            wb.save(path)
            if path.stat().st_size >= target:
                return
        if row > 600_000:
            raise RuntimeError("xlsx row guard exceeded")


def _write_xls(path: Path, markers: list[str], target: int) -> None:
    import xlwt

    path.parent.mkdir(parents=True, exist_ok=True)
    wb = xlwt.Workbook()
    ws = wb.add_sheet("legacy_sheet")
    for i, m in enumerate(markers):
        ws.write(i, 0, m)
        ws.write(i, 1, "marker")
    row = len(markers)
    while True:
        filler = "XlsLegacyPad " + hashlib.sha256(str(row).encode()).hexdigest()
        ws.write(row, 0, filler)
        ws.write(row, 1, row)
        row += 1
        if row >= 65530:
            raise RuntimeError("xls row limit reached before target size; widen cells or split sheets")
        if row % 1200 == 0:
            wb.save(path)
            if path.stat().st_size >= target:
                return


def _write_pptx(path: Path, markers: list[str], target: int) -> None:
    from pptx import Presentation
    from pptx.util import Inches, Pt

    prs = Presentation()
    prs.slide_width = Inches(10)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]
    path.parent.mkdir(parents=True, exist_ok=True)
    for i, m in enumerate(markers):
        salt = hashlib.sha256(str(i).encode()).hexdigest()
        body = ("\n".join(f"PptxSlideRegressionPad{s}_{salt[:48]}" for s in range(24))) + "\n"
        slide = prs.slides.add_slide(blank)
        box = slide.shapes.add_textbox(Inches(0.4), Inches(0.4), Inches(9.2), Inches(6.8))
        tf = box.text_frame
        tf.clear()
        p = tf.paragraphs[0]
        p.text = body
        p.font.size = Pt(9)
        p2 = tf.add_paragraph()
        p2.text = m
        p2.font.size = Pt(12)
    slide_n = len(markers)
    while True:
        salt = hashlib.sha256(str(slide_n).encode()).hexdigest()
        body = ("\n".join(f"PptxSlideRegressionPad{s}_{salt[:48]}" for s in range(48))) + "\n"
        slide = prs.slides.add_slide(blank)
        box = slide.shapes.add_textbox(Inches(0.4), Inches(0.4), Inches(9.2), Inches(6.8))
        tf = box.text_frame
        tf.clear()
        p = tf.paragraphs[0]
        p.text = body
        p.font.size = Pt(9)
        slide_n += 1
        if slide_n % 40 == 0:
            prs.save(path)
            if path.stat().st_size >= target:
                return
        if slide_n > 12000:
            raise RuntimeError("pptx slide guard exceeded before reaching target size")


def _write_pdf(path: Path, markers: list[str], target: int) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    path.parent.mkdir(parents=True, exist_ok=True)
    tail_pages = max(600, min(80_000, target // 400))

    for lines_pp in range(48, 420, 12):
        c = canvas.Canvas(str(path), pagesize=letter)
        _, h = letter
        page = 0
        for m in markers:
            c.setFont("Helvetica-Bold", 12)
            c.drawString(40, h - 50, m)
            c.setFont("Helvetica", 7)
            salt = hashlib.sha256(m.encode()).hexdigest()
            filler = "PdfRegressionPad " + salt + " " + ("z" * 72)
            y = h - 90
            for _ in range(min(lines_pp, 80)):
                c.drawString(40, y, filler[:118])
                y -= 7
                if y < 52:
                    break
            c.showPage()
            page += 1

        marker_done_at = page - 1
        while page < 120_000:
            salt = hashlib.sha256(str(page).encode()).hexdigest()
            filler = "PdfRegressionPad " + salt + " " + ("z" * 72)
            y = h - 72
            c.setFont("Helvetica", 7)
            for _ in range(lines_pp):
                c.drawString(40, y, filler[:118])
                y -= 7
                if y < 52:
                    break
            c.showPage()
            page += 1
            if page >= marker_done_at + tail_pages:
                break
        c.save()
        if path.stat().st_size >= target:
            return
    raise RuntimeError("pdf: could not reach target size; tune lines_pp / tail_pages")


def _write_md(path: Path, markers: list[str], target: int) -> None:
    _write_txt_like(path, markers, target, sep="\n\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--target-mib",
        type=int,
        default=52,
        help="Minimum file size per artifact (default 52 MiB)",
    )
    args = ap.parse_args()
    # Default 52 MiB for Lisa runs; use --target-mib 1 for fast smoke tests.
    target = max(int(args.target_mib), 1) * 1024 * 1024
    if target < 50 * 1024 * 1024:
        print("WARNING: target below 50 MiB Lisa requirement", flush=True)

    os.environ.setdefault("LOCAL_RAG_DATA_DIR", "")
    from pipecat_bots.local_rag import rag_documents_dir

    corpus_root = rag_documents_dir() / "enterprise_regression"
    corpus_root.mkdir(parents=True, exist_ok=True)

    plan = [
        ("txt", "corpus_enterprise.txt", lambda p, m, t: _write_txt_like(p, m, t)),
        ("md", "corpus_enterprise.md", lambda p, m, t: _write_md(p, m, t)),
        ("markdown", "corpus_enterprise_long.markdown", lambda p, m, t: _write_md(p, m, t)),
        ("csv", "corpus_enterprise.csv", lambda p, m, t: _write_csv(p, m, t)),
        ("docx", "corpus_enterprise.docx", lambda p, m, t: _write_docx(p, m, t)),
        ("xlsx", "corpus_enterprise.xlsx", lambda p, m, t: _write_xlsx(p, m, t)),
        ("xls", "corpus_enterprise.xls", lambda p, m, t: _write_xls(p, m, t)),
        ("pptx", "corpus_enterprise.pptx", lambda p, m, t: _write_pptx(p, m, t)),
        ("pdf", "corpus_enterprise.pdf", lambda p, m, t: _write_pdf(p, m, t)),
    ]

    manifest_files: list[dict] = []
    for kind, fname, fn in plan:
        markers = _markers(kind)
        out = corpus_root / fname
        print(f"Generating {out.name} ({target} bytes target)...", flush=True)
        fn(out, markers, target)
        sz = out.stat().st_size
        _ensure_min_size(out, target)
        manifest_files.append(
            {
                "kind": kind,
                "filename": fname,
                "rel_path": str(Path("enterprise_regression") / fname),
                "bytes_on_disk": sz,
                "markers": markers,
            }
        )
        print(f"  wrote {sz} bytes", flush=True)

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target_bytes": target,
        "corpus_root": str(corpus_root.resolve()),
        "files": manifest_files,
    }
    man_path = corpus_root / "manifest.json"
    man_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Wrote manifest {man_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

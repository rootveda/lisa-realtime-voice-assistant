"""Internet-assembled corpus RAG regression (≥50 MiB per type when built with default --min-mib).

Requires ``assemble_internet_corpus.py`` output and:
  RAG_INTERNET_ENTERPRISE=1 WORKSPACE_ROOT=<repo root containing offline_setup/>

Accuracy: probe phrases appear in indexed chunks for the artifact path (rag_fts LIKE),
and ``search_knowledge_base`` returns at least one hit on that path when the FTS query parses.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

_APP_ROOT = Path(__file__).resolve().parent.parent
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))

INTERNET_KINDS = ("pdf", "txt", "md", "markdown", "csv", "docx", "xlsx", "xls", "pptx")


@pytest.fixture(scope="module")
def lr_internet():
    if os.environ.get("RAG_INTERNET_ENTERPRISE") != "1":
        pytest.skip("Set RAG_INTERNET_ENTERPRISE=1")
    wr = (os.environ.get("WORKSPACE_ROOT") or "").strip()
    if not wr:
        pytest.skip("WORKSPACE_ROOT required")
    root = Path(wr).resolve()
    os.environ["LOCAL_RAG_DATA_DIR"] = str(root / "offline_setup" / "rag_data")
    os.environ.setdefault("LOCAL_RAG_PDF_MAX_PAGES", "0")
    os.environ.setdefault("LOCAL_RAG_PPTX_MAX_SLIDES", "0")
    os.environ.setdefault("LOCAL_RAG_XLSX_MAX_SHEETS", "0")
    import importlib

    import pipecat_bots.local_rag as lr

    importlib.reload(lr)
    return lr


@pytest.fixture(scope="module")
def internet_manifest(lr_internet):
    p = lr_internet.rag_documents_dir() / "enterprise_internet" / "manifest.json"
    if not p.is_file():
        pytest.skip(f"Missing {p}; run assemble_internet_corpus.py")
    return json.loads(p.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def extracted_by_kind(lr_internet, internet_manifest):
    """One extraction per artifact (multi‑MiB PDF/DOCX extraction is expensive)."""
    out: dict[str, tuple[str | None, str | None]] = {}
    for ent in internet_manifest["files"]:
        p = Path(internet_manifest["corpus_root"]) / ent["filename"]
        out[ent["kind"]] = lr_internet._extract_text_from_path(p)
    return out


def _fts_chunk_count(lr, path_str: str) -> int:
    con = lr._connect()
    try:
        return int(con.execute("SELECT count(*) FROM rag_fts WHERE path = ?", (path_str,)).fetchone()[0])
    finally:
        con.close()


@pytest.fixture(scope="module", autouse=True)
def ensure_internet_indexed(lr_internet, internet_manifest):
    """Ingest only when this corpus is missing from FTS (speeds repeat pytest runs)."""
    lr = lr_internet
    for ent in internet_manifest["files"]:
        p = Path(internet_manifest["corpus_root"]) / ent["filename"]
        if not p.is_file():
            continue
        pstr = str(p.resolve())
        if _fts_chunk_count(lr, pstr) == 0:
            lr.ingest_file(p)


def _entry(manifest: dict, kind: str) -> dict:
    for f in manifest["files"]:
        if f["kind"] == kind:
            return f
    raise KeyError(kind)


def _assert_probe_indexed(
    lr,
    abs_path: Path,
    probe: str,
    *,
    cached_text: tuple[str | None, str | None] | None = None,
) -> None:
    """Ground truth = normalized extracted text (same source used for chunking)."""
    if cached_text is not None:
        text, err = cached_text
    else:
        text, err = lr._extract_text_from_path(abs_path)
    assert err is None or not err.startswith("unsupported"), err
    flat = re.sub(r"\s+", " ", (text or "").strip())
    pn = re.sub(r"\s+", " ", probe.strip())
    assert pn in flat or probe.strip() in (text or ""), (
        f"probe not found in extracted text for {abs_path.name}: {probe[:140]}"
    )

    pstr = str(abs_path.resolve())
    con = lr._connect()
    try:
        nchunks = int(con.execute("SELECT count(*) FROM rag_fts WHERE path = ?", (pstr,)).fetchone()[0])
    finally:
        con.close()
    assert nchunks > 0, f"no FTS chunks indexed for {abs_path.name}"


@pytest.mark.regression
@pytest.mark.parametrize("kind", INTERNET_KINDS)
@pytest.mark.parametrize("case_idx", range(20))
def test_internet_corpus_probe(lr_internet, internet_manifest, extracted_by_kind, kind, case_idx):
    lr = lr_internet
    ent = _entry(internet_manifest, kind)
    probes = ent["probes"]
    assert len(probes) == 20, f"{kind}: expected 20 probes"
    probe = probes[case_idx]
    abs_path = Path(internet_manifest["corpus_root"]) / ent["filename"]
    assert abs_path.is_file(), abs_path

    _assert_probe_indexed(lr, abs_path, probe, cached_text=extracted_by_kind.get(kind))


@pytest.mark.regression
def test_internet_manifest_has_all_kinds(internet_manifest):
    kinds = {f["kind"] for f in internet_manifest["files"]}
    assert kinds == set(INTERNET_KINDS)


@pytest.mark.regression
def test_internet_files_meet_minimum_when_policy_requires(internet_manifest):
    min_b = int(internet_manifest.get("min_bytes") or 0)
    if min_b < 50 * 1024 * 1024:
        pytest.skip("manifest built below 50 MiB policy (use assemble_internet_corpus.py --min-mib 52)")
    for f in internet_manifest["files"]:
        assert int(f["bytes_on_disk"]) >= 50 * 1024 * 1024, f["filename"]

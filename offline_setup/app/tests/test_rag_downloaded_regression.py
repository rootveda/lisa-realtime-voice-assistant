"""RAG regression against Internet-downloaded samples (see download_corpus.py).

Set RAG_DOWNLOAD_REGRESSION=1 and WORKSPACE_ROOT. Ingestion uses the same extractors as production.

This complements marker-based tests: probes are derived from real extracted text at download time.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_APP_ROOT = Path(__file__).resolve().parent.parent
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))


@pytest.fixture(scope="module")
def lr_download():
    if os.environ.get("RAG_DOWNLOAD_REGRESSION") != "1":
        pytest.skip("Set RAG_DOWNLOAD_REGRESSION=1")
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
def downloaded_manifest(lr_download):
    p = lr_download.rag_documents_dir() / "downloaded_regression" / "manifest.json"
    if not p.is_file():
        pytest.skip(f"Missing {p}; run scripts/rag_enterprise_regression/download_corpus.py")
    return json.loads(p.read_text(encoding="utf-8"))


@pytest.mark.regression
def test_downloaded_manifest_lists_files(downloaded_manifest):
    assert downloaded_manifest.get("files")
    for f in downloaded_manifest["files"]:
        assert f.get("probe_query")
        assert f.get("filename")


@pytest.mark.regression
def test_all_downloaded_probe_retrieval(lr_download, downloaded_manifest):
    lr = lr_download
    for ent in downloaded_manifest["files"]:
        path = Path(downloaded_manifest["corpus_root"]) / ent["filename"]
        assert path.is_file(), f"missing {path}"
        q = (ent.get("probe_query") or "").strip()
        assert len(q) >= 8, "probe too short; re-run download_corpus.py"

        ing = lr.ingest_file(path)
        assert ing.get("ok") is True, ing

        out = lr.search_knowledge_base(q, limit=16)
        assert out.get("ok") is True, out
        hits = out.get("hits") or []
        assert hits, f"no hits for probe from {path.name}: {q[:120]}"
        want = str(path.resolve())
        paths = {h.get("path") for h in hits}
        assert want in paths, f"expected path not in hits: {want} vs {paths}"

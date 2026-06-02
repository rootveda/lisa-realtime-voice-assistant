#!/usr/bin/env python3
"""Download a small set of public sample documents for RAG regression (Internet corpus).

Why not “random” URLs: unfettered downloads are a security and reproducibility risk (malware,
paywalls, churn). This script uses a **curated pool** of stable, openly accessible files and
optionally picks ``--count`` of them with a fixed RNG seed so runs stay repeatable.

After download, extracts text via the same pipeline as ingestion and writes **probe phrases**
into ``manifest.json`` for deterministic FTS tests (no synthetic markers required).

Usage::
  export WORKSPACE_ROOT=/path/to/lisa/nvidia_voice
  python3 scripts/rag_enterprise_regression/download_corpus.py --count 3 --seed 7
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

_APP_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))


# Curated direct links (HTTPS). Extend this list as needed.
_DEFAULT_POOL: list[tuple[str, str]] = [
    (
        "https://mozilla.github.io/pdf.js/web/compressed.tracemonkey-pldi-09.pdf",
        "sample_mozilla_tracemonkey.pdf",
    ),
    (
        "https://www.gutenberg.org/files/1342/1342-0.txt",
        "gutenberg_pride_and_prejudice.txt",
    ),
    (
        "https://raw.githubusercontent.com/datasets/country-codes/master/data/country-codes.csv",
        "country_codes.csv",
    ),
    (
        "https://raw.githubusercontent.com/github/gitignore/main/Python.gitignore",
        "python_gitignore.txt",
    ),
    (
        "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf",
        "w3c_dummy.pdf",
    ),
]


def _pick_probe_phrase(text: str) -> str:
    """Choose a short phrase likely to survive FTS5 AND tokenization."""
    if not text or not text.strip():
        return "placeholder"
    words = re.findall(r"[A-Za-z]{4,}", text)
    if len(words) < 6:
        blob = re.sub(r"\s+", " ", text.strip())[:240]
        return blob if len(blob) >= 12 else "document"
    # Skip boilerplate at start: sample middle-ish window of words.
    start = max(0, min(len(words) // 5, len(words) - 6))
    phrase = " ".join(words[start : start + 6])
    return phrase[:180]


def _download(url: str, dest: Path, *, timeout: int = 120) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; nvidia_voice RAG regression; +https://example.invalid)",
            "Accept": "*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    dest.write_bytes(data)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", type=int, default=3, help="How many files to download from the pool")
    ap.add_argument("--seed", type=int, default=42, help="RNG seed for which URLs are chosen")
    ap.add_argument(
        "--list-only",
        action="store_true",
        help="Print the default URL pool and exit",
    )
    args = ap.parse_args()

    if args.list_only:
        for u, n in _DEFAULT_POOL:
            print(f"{n}\t{u}")
        return 0

    import os

    os.environ.setdefault("LOCAL_RAG_DATA_DIR", "")
    from pipecat_bots.local_rag import _extract_text_from_path, rag_documents_dir

    pool = list(_DEFAULT_POOL)
    rng = random.Random(args.seed)
    rng.shuffle(pool)
    n = max(1, min(args.count, len(pool)))
    chosen = pool[:n]

    out_dir = rag_documents_dir() / "downloaded_regression"
    out_dir.mkdir(parents=True, exist_ok=True)

    entries = []
    for url, fname in chosen:
        dest = out_dir / fname
        print(f"Downloading {url} -> {dest.name} ...", flush=True)
        try:
            _download(url, dest)
        except Exception as e:
            print(f"  FAILED: {e}", flush=True)
            continue
        text, err = _extract_text_from_path(dest)
        if err:
            print(f"  extract warning ({dest.name}): {err}", flush=True)
        body = (text or "").strip()
        probe = _pick_probe_phrase(body)
        entries.append(
            {
                "filename": fname,
                "rel_path": str(Path("downloaded_regression") / fname),
                "source_url": url,
                "bytes_on_disk": dest.stat().st_size,
                "extract_error": err,
                "probe_query": probe,
                "extract_chars": len(body),
            }
        )
        print(f"  wrote {dest.stat().st_size} bytes; probe={probe[:80]}…", flush=True)

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "kind": "internet_download",
        "seed": args.seed,
        "count_requested": args.count,
        "corpus_root": str(out_dir.resolve()),
        "files": entries,
    }
    man_path = out_dir / "manifest.json"
    man_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Wrote {man_path}", flush=True)
    return 0 if entries else 1


if __name__ == "__main__":
    raise SystemExit(main())

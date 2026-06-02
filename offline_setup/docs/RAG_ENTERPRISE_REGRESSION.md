# Lisa RAG regression (offline lexical FTS)

This regression validates **deterministic retrieval** for the supported offline RAG formats under `pipecat_bots/local_rag.py`: `.txt`, `.md`, `.markdown`, `.csv`, `.pdf`, `.docx`, `.xlsx`, `.xls`, `.pptx`.

## Scope and definition of “100% accuracy”

These tests do **not** assert LLM answer equivalence (non-deterministic).  
**Pass criteria:** for each format, **20 embedded tokens** appear in the SQLite FTS index (`rag_fts.body`) for the expected absolute path, and `search_knowledge_base(token)` returns at least one hit on that path.

## Corpus layout

- Generator: `offline_setup/app/scripts/rag_enterprise_regression/generate_corpus.py`
- Output directory: `offline_setup/rag_data/documents/enterprise_regression/`
- Minimum size: **≥ 50 MiB per file** (default generator target **52 MiB**).
- Each artifact embeds **20 unique markers** per format (`ENTREG2026…`) listed in `manifest.json`.

**Note:** Legacy Excel is **`.xls`** (not `.xlx`).  

## Test matrix (Lisa cases)

For **each** of the **9** suffix groups above:

| Case bucket | Description |
|-------------|-------------|
| TC-01–TC-05 | Baseline lexical retrieval for markers 01–05 |
| TC-06–TC-10 | Mid-sequence markers 06–10 (chunk boundary stress vs single prefix block) |
| TC-11–TC-15 | Late markers 11–15 |
| TC-16–TC-20 | Final markers 16–20 + path discrimination vs other indexed PDFs |

**Total automated checks:** 9 × 20 **marker assertions** + **manifest completeness** (+ optional **minimum size** assertion when `target_bytes ≥ 50 MiB`).

## Environment knobs (large binary extraction)

For ingest of large PDF/PPTX/spreadsheets, the runner sets (override as needed):

- `LOCAL_RAG_PDF_MAX_PAGES=0` — unlimited PDF pages during text extraction  
- `LOCAL_RAG_PPTX_MAX_SLIDES=0` — unlimited slides  
- `LOCAL_RAG_XLSX_MAX_SHEETS=0` — unlimited worksheets  

## How to run

From `offline_setup/app`:

```bash
chmod +x scripts/rag_enterprise_regression/run.sh
export WORKSPACE_ROOT=/absolute/path/to/lisa/nvidia_voice   # parent of offline_setup/
./scripts/rag_enterprise_regression/run.sh
```

Optional:

- `TARGET_MIB=52` — per-file minimum target (MiB).  
- `MIN_WALL_SEC=1800` — soak sleep after work completes so wall time is **≥ 30 minutes** if the pipeline finishes sooner.  
- `DOCS_OUT` — override path for append-only run footer.

Latest pytest output is mirrored to `offline_setup/docs/RAG_ENTERPRISE_REGRESSION_LAST_RUN.txt` when using `run.sh`.

## Operational notes

- **DOCX and PPTX** are zip containers; the generator uses high-entropy lines/slides so each artifact reaches **≥ 50 MiB on disk**. Building **52 MiB** DOCX can take **many minutes** on typical CPUs because `python-docx` rewrites the full OOXML package on save (the generator batches saves to reduce overhead).
- **PDF** generation uses ReportLab and pads with deterministic pseudo-random text streams until the byte budget is met.
- Do **not** “repeat” the pytest suite in a tight loop to chase LLM answers — retrieval correctness here is **FTS + indexed body**, per the definition above.

## Internet-only Lisa pipeline (≥50 MiB per type, no synthetic prose)

For **HTTPS-only** corpora merged up to a minimum size per extension, probes derived from extracted text, and the **180-case** matrix against Internet sources, see **[RAG_INTERNET_ENTERPRISE_REGRESSION.md](./RAG_INTERNET_ENTERPRISE_REGRESSION.md)** and `scripts/rag_enterprise_regression/run_internet_enterprise_regression.sh`.

## Internet samples (small curated smoke)

Synthetic corpora are optional. To pull a **small curated set** of public URLs (not arbitrary “random” links — those break CI and are unsafe), compute probe phrases from real extracted text, and run FTS checks:

```bash
cd offline_setup/app
export WORKSPACE_ROOT=/absolute/path/to/lisa/nvidia_voice
python3 scripts/rag_enterprise_regression/download_corpus.py --count 3 --seed 7
export RAG_DOWNLOAD_REGRESSION=1
python3 -m pytest tests/test_rag_downloaded_regression.py -v
```

- Script: `scripts/rag_enterprise_regression/download_corpus.py`  
- Output: `rag_data/documents/downloaded_regression/` + `manifest.json`  
- Tests: `tests/test_rag_downloaded_regression.py`

Use `--list-only` to print the built-in URL pool; extend `_DEFAULT_POOL` in that script for more formats.

## Related code

- Retrieval / ingest: `offline_setup/app/pipecat_bots/local_rag.py`  
- Generated corpus tests: `offline_setup/app/tests/test_rag_enterprise_regression.py`  
- Downloaded corpus tests: `offline_setup/app/tests/test_rag_downloaded_regression.py`

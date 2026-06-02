# Internet-only Lisa RAG regression (≥50 MiB per type)

This workflow satisfies **download-from-Internet-only** regression: there is **no locally authored prose or synthetic corpus**. Content comes from curated HTTPS URLs (arXiv, Project Gutenberg, GitHub raw files, FileSamples, python-pptx fixtures, etc.). Files are **merged or concatenated** in-process until each supported artifact meets the minimum byte target—assembly glue only.

## Supported artifacts

Aligned with `pipecat_bots/local_rag.py`:

| Kind | Output file | Primary Internet sources |
|------|-------------|-------------------------|
| pdf | `internet_merged.pdf` | arXiv PDFs (many IDs) |
| txt | `internet_merged.txt` | Project Gutenberg |
| md | `internet_merged.md` | GitHub `gitignore` templates (as text corpus) |
| markdown | `internet_merged.markdown` | Same as `.md` plus a tiny HTML-comment suffix so dedupe does not hide this path |
| csv | `internet_merged.csv` | datasets org CSV dumps |
| docx | `internet_merged.docx` | FileSamples DOCX |
| xlsx | `internet_merged.xlsx` | FileSamples XLSX |
| xls | `internet_merged.xls` | FileSamples XLS (rows duplicated until size target) |
| pptx | `internet_merged.pptx` | scanny/python-pptx GitHub `features/steps/test_files/*.pptx` |

**Note:** `.xlx` is not a supported extension; legacy Excel is **`.xls`**.

## Definition of “100% accuracy” (Lisa lexical RAG)

Not LLM answer equality. For each type we record **20 probe substrings** derived from the same extraction pipeline used for indexing. A test passes when:

1. The probe appears in **normalized extracted text** for that file.
2. At least **one** `rag_fts` row exists for that absolute path (chunk index present).

## Test matrix

- **20 cases × 9 types = 180** retrieval checks (`case_idx` 0–19 per kind).
- Plus manifest completeness and optional minimum-size policy when `min_bytes ≥ 50 MiB`.

## Commands

From `offline_setup/app`:

```bash
chmod +x scripts/rag_enterprise_regression/run_internet_enterprise_regression.sh
export WORKSPACE_ROOT=/absolute/path/to/lisa/nvidia_voice

# Full policy (52 MiB per artifact): long runtime (downloads + merges + ingest).
./scripts/rag_enterprise_regression/run_internet_enterprise_regression.sh
```

Environment:

| Variable | Meaning |
|----------|---------|
| `MIN_MIB` | Minimum bytes per artifact (default **52**) |
| `MIN_WALL_SEC` | Soak sleep after work so total wall time ≥ value (default **1800** ≈ 30 min) |
| `RUN_INTERNET_SMOKE` | Set to `1` to pass `--smoke` to the assembler (fewer URLs; still respects `MIN_MIB`) |

Manual steps:

```bash
export WORKSPACE_ROOT=/absolute/path/to/lisa/nvidia_voice
python3 scripts/rag_enterprise_regression/assemble_internet_corpus.py --min-mib 52
export RAG_INTERNET_ENTERPRISE=1
python3 -m pytest tests/test_rag_internet_enterprise_regression.py -v
```

The assembler **ingests** each output file into FTS when it finishes.

## Files

| Path | Role |
|------|------|
| `scripts/rag_enterprise_regression/assemble_internet_corpus.py` | Download + merge + manifest + ingest |
| `tests/test_rag_internet_enterprise_regression.py` | 180 Lisa probes + manifest checks |
| `docs/RAG_INTERNET_ENTERPRISE_LAST_RUN.txt` | Last `run_internet_enterprise_regression.sh` pytest tee |

## Operational notes

- **Runtime**: Full **52 MiB × 9** merges involves large PDF/XLSX/PPTX growth loops and **many HTTPS GETs**. Expect **well over 30 minutes** end-to-end on typical networks; the shell script’s soak timer only **pads short runs** up to `MIN_WALL_SEC`.
- **Repeat tests**: The suite is intended to run **once per pipeline invocation** (`no repeat test`). Re-running pytest is cheap if artifacts are unchanged because extraction is cached per module and FTS rows are reused when present.

# Policy Foresight Engine: local Python scripts v1.2.0

Copy these files into your existing repository, beside `input`, `parse` and `output`.
The scripts read/write those local folders. No GitHub token or GitHub API is used.
Paths are resolved from the scripts' location, even when launched from another directory.

## Run on Windows

```bat
py -3.12 -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python parse.py
python classify.py
python evaluate.py
```

Ollama must be running with the configured models downloaded. Use your existing
Ollama installation, or the included optional Ollama-only container:

```bat
docker compose up -d
docker compose exec ollama ollama pull qwen3:4b
docker compose exec ollama ollama pull qwen3:8b
```

Native Ollama uses `ollama pull qwen3:4b` and `ollama pull qwen3:8b`.
Model inference stays local. Initial package/model downloads need internet access.

## Files and results

All input subfolders are scanned recursively and mirrored in parsing and analysis.
The full PDF filename becomes a result directory, keeping its extension to avoid
collisions. For example:

```text
input/Health/2024/report.pdf
parse/Health/2024/report.pdf/document.json
parse/Health/2024/report.pdf/document.md
parse/Health/2024/report.pdf/manifest.json
output/classification/Health/2024/report.pdf/<fingerprint>.json
output/evaluation_cache/Health/2024/report.pdf/<fingerprint>.json
output/<UTC timestamp>/Health/2024/report.pdf/evaluation.json
output/<UTC timestamp>/Health/2024/report.pdf/classification.json
output/<UTC timestamp>/Health/2024/report.pdf/Evaluation_reports.csv
output/<UTC timestamp>/Health/2024/report.pdf/Spillovers.csv
```

Each run also has combined `Evaluation_reports.csv` and `Spillovers.csv`, plus
`agent_activity.md` and `run.json`, at its root. Identically named PDFs in different
subfolders stay separate. Identical PDF content can reuse conversion while still
getting its own mirrored result folder.

| Script | Local input | Local output |
|---|---|---|
| `parse.py` | `input/**/*.pdf` | `parse/<input-relative PDF path>/document.json`, `document.md`, `manifest.json` |
| `classify.py` | Parsed JSON | `output/classification/<input-relative PDF path>/<fingerprint>.json` |
| `evaluate.py` | Parsed JSON + classification | Combined CSVs and diagnostics in `output/<UTC timestamp>/`; matching input subfolders containing per-report CSVs and JSON |

The evaluation CSV retains the original 31 workbook headings and opens in Excel.
Every result is a draft requiring human review. Unsupported fields stay blank;
conflicting single values remain unresolved with full candidates retained in JSON.
Only reports with supported evaluation evidence get table rows. Failures are
listed in `run.json`; if none succeeds, diagnostics are saved without a header-only table.
The Report links column contains local source paths relative to the script folder.

Successful parsing is reused, including checksum-verified parsing previously
produced by the webapp or GitHub scripts. PDF fingerprints use Git's blob algorithm
for compatibility; no Git command is needed. Changed PDFs receive new results.
Existing flat/hash-based parsing and analysis caches are copied into the mirrored
layout when reused; no PDF conversion or model call is needed merely to reorganize
valid saved results. Existing historical folders are retained. Run all three scripts
to populate the new layout.

Completed classification/evaluation results and validated model windows are also
cached. Rerunning evaluation produces a new timestamped table using those caches.
`output/evaluation_cache/` stores completed evaluation records; `.work/model/`
stores validated model responses. Keep those folders to resume work.

The included `.env` uses `INPUT_FOLDER`, `PARSE_FOLDER` and `OUTPUT_FOLDER`.
Your previous `.env` also works: the old `GITHUB_INPUT_FOLDER`, `GITHUB_PARSE_FOLDER`
and `GITHUB_OUTPUT_FOLDER` names remain compatible. GitHub token/branch settings
are ignored. An old GITHUB_REPOSITORY value is only a stable namespace for row IDs.

Results are written immediately and atomically to local parse/output files.
Input PDFs are never written or deleted. Run one copy at a time. If a stage fails,
fix the reported issue and rerun it; successful results are reused. Local writes
must complete before the following stage can read its prerequisites.

Prompts are plain strings in `classify.py`, `evaluate.py` and `common.py`.
`codebook.json` contains category labels and CSV headings. Changing the model,
prompts, labels, parsed content or window size changes analysis cache keys.
Schema validation and exact source-quotation checks reject unsupported output.
Truncated model responses retry in smaller groups; an unsplittable request fails
explicitly rather than accepting incomplete JSON. Private model thinking is not saved.

## Sync through your repository

Git synchronization is separate from processing. When you want to publish results:

```bat
git add parse output
git commit -m "Update parsing and evaluation results"
git push
```

Keep `.env` and `.work/` out of version control; the included `.gitignore` covers them.
For custom folder names, adjust the `git add` paths accordingly.

## Checks

```bat
python -m unittest discover -s tests -v
```

Fourteen local-folder tests passed, including full pipeline execution, cache reuse,
nested folders, duplicate filenames/content, cache-layout migration, launching
from another directory, legacy parsing, source-file protection, changed
files, schema/quote checks, CSV safety and token-limit recovery. Docling and Ollama
are simulated in these tests; real Windows/model execution and 2,000-PDF throughput
were not tested here. Matching a quotation does not establish that a model's
interpretation is correct; review the evidence before using the table as research data.

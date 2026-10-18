# WS1 scenario extraction pipeline

Three scripts, run in order. Each step writes everything it did to disk so the methods section can be reproduced exactly.

## 0. Setup

```
pip install pymupdf rapidfuzz openai pyyaml pandas openpyxl jsonschema
export OPENROUTER_API_KEY=...
```

Make `corpus.csv` with one row per included paper (see `corpus_template.csv`):

| column | what goes in it |
|---|---|
| paper_id | R#### record ID (G## for grey literature) |
| source_type | charting-form category: peer-reviewed, preprint, institutional report, government, AI lab evaluation, red-team study |
| file_path | path to the PDF (or a .txt if you've already extracted it) |

Then open `config.yaml` and check the three model IDs against OpenRouter's model list, and fill in a provider name for each so the host is pinned.

## Building corpus.csv from Rayyan

```
python build_corpus.py --ris articles.ris --screening ~/spar-lit-review/to_screen.csv --out data
```

Includes a record if Ishani voted Include and nobody voted Exclude. Records where the screeners disagree go to `data/conflicts.csv` and stay out until resolved. `source_type` is assigned by fixed rules (see `data/source_type_log.csv` for which rule fired); anything the rules can't place is `needs_check`, and `sample.py` refuses to run until those are labeled. `file_path` starts blank; fill it in as PDFs are collected.

## Getting the PDFs

```
python3 fetch_pdfs.py                 # downloads open-access copies into pdfs/, fills file_path
python3 verify_pdfs.py                # checks each PDF is the right paper (needs pymupdf)
```

Anything `verify_pdfs.py` marks as a mismatch: move it to `pdfs/_rejected/` and add the URL to `data/rejected_urls.txt` (paper_id, reason, URL, tab-separated) so it's never downloaded again. Papers with no open copy are listed in `data/needs_manual.csv`; get them through the library, save as `pdfs/<paper_id>.pdf`, and re-run `fetch_pdfs.py` to fill in their paths.

## 1. Draw the samples

```
python sample.py --corpus corpus.csv --seed <pick a number and record it>
```

Writes `samples/validation_ids.txt` (the 14 papers all three coders do), `samples/pilot_ids.txt` (5 papers outside that set), and `samples/sampling_record.json` with the per-type counts and seed. Coders should start on the validation papers before seeing any model output.

## 2. Run the pilot

```
python extract.py --corpus corpus.csv --ids samples/pilot_ids.txt --models all
```

3 models x 5 papers x 3 runs = 45 calls. Each call is saved under `runs/raw/<model>/<paper>/runN.json` with the model ID, provider, prompt hash, temperature, timestamp, raw reply, and status (ok, json_error, schema_error, refusal, truncated, api_error). Re-running skips finished calls and retries API errors. Use `--mock` to test the plumbing without spending anything.

## 3. Automatic checks + review sheet

```
python check.py --runs runs
```

Prints per-model numbers (JSON failures, refusals, quote check, stability) and writes `runs/review_sheet.xlsx`. Models appear as codes A/B/C so reviewers rate blind; the key is in `runs/blinding_key.json`.

The quote check only shows that a quote exists in the paper. Whether it supports the scenario, and what the model missed, is the manual review in `REVIEW_RUBRIC.md`. Agree on the rubric before opening the sheet.

## Known limits

- Papers longer than a model's context window will come back as `api_error`. The log records approximate token counts, so you can see which ones. We'd handle those by hand or decide on a chunking rule.
- Scanned PDFs with no text layer give empty text. Check `runs/text/` for any near-empty files.
- Grouping the same scenario across runs is fuzzy (similarity >= 85 on quote or summary). Reviewers can mark leftovers with `duplicate_of`.

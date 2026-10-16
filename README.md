# WS1 scenario extraction pipeline

Pulls AI-enabled biological misuse scenarios out of the papers in the WS1 systematic review, using several LLMs, and builds a blinded sheet so people can check how well each model did.

There are three scripts, run in order: `sample.py` picks which papers to use, `extract.py` runs the models over them, and `check.py` scores the results and writes the review sheet. Each step writes everything it did to disk, so the methods section can be reproduced exactly.

## What's in this folder

| file | what it is |
|---|---|
| `sample.py` | draws the human-coded validation sample and the pilot set |
| `extract.py` | sends each paper to each model and saves every reply |
| `check.py` | quote check, run-to-run stability, and the review spreadsheet |
| `config.yaml` | models, temperature, runs per paper, API endpoint |
| `prompts/extract_v1.txt` | the extraction prompt (scenario definition + output format) |
| `REVIEW_RUBRIC.md` | how reviewers rate the model output |
| `corpus_template.csv` | example of the corpus file you need to make |

## 0. Setup

You need Python 3 and an [OpenRouter](https://openrouter.ai) API key. Run everything from inside this folder.

```
python3 -m venv .venv
source .venv/bin/activate
pip install pymupdf rapidfuzz openai pyyaml pandas openpyxl jsonschema
export OPENROUTER_API_KEY=...
```

Keep the key in the environment variable. Never put it in `config.yaml` or commit it.

### Make the corpus file

Make `corpus.csv` with one row per included paper (copy `corpus_template.csv` to start):

| column | what goes in it |
|---|---|
| paper_id | stable ID, e.g. the Rayyan ID |
| source_type | `peer_reviewed`, `preprint`, `gov_institutional_report`, or `lab_eval_redteam` |
| file_path | path to the PDF, or to a `.txt` if you've already extracted the text. `~` is fine. |

### Check the config

Open `config.yaml` and:

1. Check the three model IDs against OpenRouter's model list.
2. Fill in a host for each model under `provider.order` (from the model's page on OpenRouter), so the same host serves every call. With `allow_fallbacks: false` the call fails instead of quietly switching hosts. If `order` is left empty, OpenRouter picks the host.

The other settings (3 runs per paper, temperature 0, 16k max output tokens) are what the protocol uses. Every run copies the config and prompt into its output folder, so changes are always on record.

## 1. Test the plumbing (free)

Before spending anything, do a dry run with `--mock`. It uses a fake model that quotes real sentences from each paper plus one made-up quote, so you can see the quote check catch it.

```
python sample.py --corpus corpus.csv --seed 1 --out samples_mock
python extract.py --corpus corpus.csv --ids samples_mock/pilot_ids.txt --models all --mock --out runs_mock
python check.py --runs runs_mock
```

Open `runs_mock/review_sheet.xlsx` to see what reviewers will get. Then delete `samples_mock/` and `runs_mock/`.

Always give mock runs their own `--out` folder. `extract.py` skips any call that already has a saved result, so mock results left in `runs/` would stop the real models from ever running on those papers.

## 2. Draw the samples

```
python sample.py --corpus corpus.csv --seed <pick a number and record it>
```

This writes three files to `samples/`:

- `validation_ids.txt`: 10% of the corpus, stratified by `source_type` (the 14 papers all three coders do).
- `pilot_ids.txt`: 5 papers outside that set, at least one per source type where possible.
- `sampling_record.json`: the seed, per-type counts, and the IDs drawn.

The same corpus and seed always give the same draw, so record the seed in the protocol. Coders should start on the validation papers before seeing any model output.

## 3. Run the pilot

```
python extract.py --corpus corpus.csv --ids samples/pilot_ids.txt --models all
```

3 models x 5 papers x 3 runs = 45 calls. Progress prints one line per call:

```
minimax-m3         R014         run1  ok
```

Each call is saved as `runs/raw/<model>/<paper>/runN.json` with the model ID, the host that actually served it, the prompt hash, temperature, timestamp, token usage, the raw reply, the parsed JSON, and a status:

| status | meaning |
|---|---|
| `ok` | valid JSON that matches the schema |
| `schema_error` | valid JSON, but fields are missing or have wrong values |
| `json_error` | reply couldn't be parsed as JSON |
| `refusal` | reply looks like the model declined |
| `truncated` | output hit `max_output_tokens` |
| `api_error` | the call failed (network, auth, paper too long for the context window, etc.) |

If the run crashes or you stop it, just run the same command again. It skips calls that already finished and retries the ones that got `api_error`.

Useful options:

- `--models kimi-k3` or `--models minimax-m3,kimi-k3`: run only some models (keys from `config.yaml`).
- Leave out `--ids` to run every paper in the corpus.
- `--out <folder>`: save somewhere other than `runs/`.

PDF text is extracted once into `runs/text/<paper_id>.txt` and reused, so every model sees the exact same text.

## 4. Automatic checks and the review sheet

```
python check.py --runs runs
```

This prints a table per model and writes:

- `runs/review_sheet.xlsx`, with tabs:
  - `scenarios`: one row per extracted scenario, with blank reviewer columns and a dropdown for `support_rating`
  - `missed`: where reviewers log scenarios the models skipped
  - `auto_metrics`: the per-model numbers below
  - `rating_key`: the allowed ratings
- `runs/call_log.csv`: one row per model call
- `runs/blinding_key.json`: which code (A/B/C) is which model. **Don't share this with reviewers.**

What the automatic numbers mean:

- **Quote check.** Is each `source_passage` really in the paper? `exact` means found after normalizing whitespace, quotes, dashes and ligatures. `near` means a 90%+ fuzzy match (usually PDF hyphenation), worth a quick look. `not_found` means the model made it up or changed it.
- **Stability.** The three runs for a paper are grouped into scenarios, and each row shows which runs found it. `stable` means 2 or more of 3.
- **Errors.** Counts of JSON/schema errors, refusals, truncations and API errors.

Models show up as codes so reviewers rate blind. The codes are shuffled with a fixed seed (`--seed`, default 20261015), so re-running `check.py` gives the same codes.

## 5. Human review

The quote check only shows that a quote exists in the paper. Whether it supports the scenario, and what the model missed, is the manual review described in `REVIEW_RUBRIC.md`. Agree on the rubric before anyone opens the sheet.

In short: two reviewers per pilot paper work independently, rate every row as supported / partially supported / unsupported / not a scenario, log missed scenarios on the `missed` tab, and mark repeats with `duplicate_of`. The model with the fewest unsupported scenarios and the most accurate quotes wins; missed count breaks ties.

## Changing the prompt

Don't edit `prompts/extract_v1.txt` once it's been used for real runs. Copy it to `extract_v2.txt`, then update `prompt_file` and `prompt_version` in `config.yaml`, and run into a new folder (`--out runs_v2`). Otherwise the old results are skipped as "already done" and the two prompt versions get mixed together.

The prompt must keep the `{paper_id}` and `{text}` placeholders.

## Known limits

- Papers longer than a model's context window come back as `api_error`. Each record has an approximate token count (`paper_tokens_approx`), so you can see which ones. We'd handle those by hand or decide on a chunking rule.
- Scanned PDFs with no text layer give empty text. Check `runs/text/` for any near-empty files.
- Grouping the same scenario across runs is fuzzy (similarity >= 85 on quote or summary). Reviewers can mark leftovers with `duplicate_of`.

"""Run the scenario-extraction prompt over papers with one or more models.

Every call is saved to disk with its full settings, so any output can be
traced back to the exact model, provider, prompt and time that produced it.
Re-running skips calls that already finished, so a crash halfway through
costs nothing.

Usage:
  python extract.py --corpus corpus.csv --ids pilot_ids.txt --models all
  python extract.py --corpus corpus.csv --ids pilot_ids.txt --models all --mock   # no API, for testing
"""
import argparse, csv, datetime, hashlib, json, os, re, shutil, sys, time
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent

SCENARIO_SCHEMA = {
    "type": "object",
    "required": ["paper_id", "scenarios"],
    "properties": {
        "paper_id": {"type": "string"},
        "scenarios": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["summary", "extraction_type", "causal_element",
                             "ai_role", "harm", "source_passage", "justification",
                             "completeness"],
                "properties": {
                    "extraction_type": {"enum": ["direct", "inferred"]},
                    "causal_element": {"enum": ["sequence", "mechanism",
                                                "causal_relationship",
                                                "condition_to_outcome"]},
                    "completeness": {"enum": ["complete", "partial"]},
                },
            },
        },
    },
}
VALIDATOR = Draft202012Validator(SCENARIO_SCHEMA)

REFUSAL_HINTS = ("i can't help", "i cannot help", "i can't assist", "i cannot assist",
                 "i'm not able to", "i am not able to", "i won't", "unable to comply")


# ---------- paper text ----------

def pdf_to_text(pdf_path: Path) -> str:
    import pymupdf
    parts = []
    with pymupdf.open(pdf_path) as doc:
        for i, page in enumerate(doc, start=1):
            parts.append(f"[page {i}]\n{page.get_text()}")
    return "\n".join(parts)


def load_text(paper: dict, text_dir: Path) -> str:
    """Extract once and cache, so every model sees byte-identical text."""
    cached = text_dir / f"{paper['paper_id']}.txt"
    if cached.exists():
        return cached.read_text(encoding="utf-8")
    src = Path(paper["file_path"]).expanduser()
    if src.suffix.lower() == ".pdf":
        text = pdf_to_text(src)
    else:
        text = src.read_text(encoding="utf-8")
    text_dir.mkdir(parents=True, exist_ok=True)
    cached.write_text(text, encoding="utf-8")
    return text


# ---------- model output parsing ----------

def parse_json(raw: str):
    """Pull the JSON object out of a reply, tolerating code fences and stray prose."""
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(s[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None


def classify(raw: str, parsed):
    if parsed is None:
        low = raw.lower()
        if any(h in low for h in REFUSAL_HINTS):
            return "refusal", ["reply looks like a refusal"]
        return "json_error", ["could not parse JSON"]
    errs = [f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message}"
            for e in VALIDATOR.iter_errors(parsed)]
    return ("ok" if not errs else "schema_error"), errs


# ---------- model calls ----------

def call_model(cfg, model_cfg, prompt):
    from openai import OpenAI
    client = OpenAI(base_url=cfg["api_base"], api_key=os.environ[cfg["api_key_env"]],
                    timeout=cfg["request_timeout_s"])
    extra = {}
    prov = model_cfg.get("provider") or {}
    if prov.get("order"):
        extra["provider"] = prov
    resp = client.chat.completions.create(
        model=model_cfg["id"],
        messages=[{"role": "user", "content": prompt}],
        temperature=cfg["temperature"],
        max_tokens=cfg["max_output_tokens"],
        extra_body=extra or None,
    )
    choice = resp.choices[0]
    return {
        "raw": choice.message.content or "",
        "finish_reason": choice.finish_reason,
        "served_model": getattr(resp, "model", None),
        "served_provider": getattr(resp, "provider", None),
        "usage": resp.usage.model_dump() if resp.usage else None,
    }


def mock_call(paper_id, text, run):
    """Fake model for testing the plumbing. Quotes real sentences from the paper,
    plus one fabricated quote so the quote check has something to catch."""
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if 60 < len(s.strip()) < 300]
    scen = []
    for i, s in enumerate(sents[: 2 + (run % 2)]):
        scen.append({"scenario_id": f"{paper_id}-S{i+1}", "summary": f"Scenario based on: {s[:80]}",
                     "extraction_type": "direct", "causal_element": "mechanism", "actor": None,
                     "ai_role": "assists", "harm": "biological harm", "source_passage": s,
                     "page_or_section": "p1", "justification": "test", "completeness": "complete",
                     "completeness_note": ""})
    scen.append({"scenario_id": f"{paper_id}-SX", "summary": "A scenario the paper never states.",
                 "extraction_type": "inferred", "causal_element": "sequence", "actor": None,
                 "ai_role": "x", "harm": "y", "source_passage": "This sentence does not appear anywhere in the paper.",
                 "page_or_section": "?", "justification": "test", "completeness": "partial",
                 "completeness_note": ""})
    return {"raw": json.dumps({"paper_id": paper_id, "scenarios": scen}), "finish_reason": "stop",
            "served_model": "mock", "served_provider": "mock", "usage": None}


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True, help="CSV with paper_id, file_path (PDF or .txt)")
    ap.add_argument("--ids", help="text file of paper_ids to run (one per line); default all")
    ap.add_argument("--models", default="all", help="comma-separated keys from config.yaml, or 'all'")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--out", default=str(ROOT / "runs"))
    ap.add_argument("--mock", action="store_true", help="use a fake model (no API calls)")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text())
    prompt_tpl = (ROOT / cfg["prompt_file"]).read_text(encoding="utf-8")
    prompt_hash = hashlib.sha256(prompt_tpl.encode()).hexdigest()[:12]

    with open(args.corpus, newline="", encoding="utf-8") as f:
        papers = list(csv.DictReader(f))
    if args.ids:
        wanted = [l.strip() for l in Path(args.ids).read_text().splitlines() if l.strip()]
        papers = [p for p in papers if p["paper_id"] in set(wanted)]
        missing = set(wanted) - {p["paper_id"] for p in papers}
        if missing:
            sys.exit(f"paper_ids not in corpus: {sorted(missing)}")

    model_keys = list(cfg["models"]) if args.models == "all" else args.models.split(",")
    if not args.mock and not os.environ.get(cfg["api_key_env"]):
        sys.exit(f"Set {cfg['api_key_env']} first (or use --mock).")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy(args.config, out / "config_used.yaml")
    shutil.copy(ROOT / cfg["prompt_file"], out / "prompt_used.txt")
    text_dir = out / "text"

    for paper in papers:
        pid = paper["paper_id"]
        text = load_text(paper, text_dir)
        prompt = prompt_tpl.replace("{paper_id}", pid).replace("{text}", text)
        approx_tokens = len(text) // 4
        for mkey in model_keys:
            mcfg = cfg["models"][mkey]
            for run in range(1, cfg["runs_per_paper"] + 1):
                dest = out / "raw" / mkey / pid / f"run{run}.json"
                if dest.exists():
                    prev = json.loads(dest.read_text(encoding="utf-8"))
                    if prev.get("status") != "api_error":
                        continue  # done; API errors get retried
                dest.parent.mkdir(parents=True, exist_ok=True)
                t0 = time.time()
                record = {
                    "paper_id": pid, "model_key": mkey, "model_id": mcfg["id"],
                    "provider_pin": mcfg.get("provider"), "run": run,
                    "temperature": cfg["temperature"], "max_output_tokens": cfg["max_output_tokens"],
                    "prompt_version": cfg["prompt_version"], "prompt_sha256_12": prompt_hash,
                    "paper_chars": len(text), "paper_tokens_approx": approx_tokens,
                    "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "mock": args.mock,
                }
                try:
                    res = mock_call(pid, text, run) if args.mock else call_model(cfg, mcfg, prompt)
                    parsed = parse_json(res["raw"])
                    status, errs = classify(res["raw"], parsed)
                    if res.get("finish_reason") == "length":
                        status, errs = "truncated", errs + ["output hit max tokens"]
                    record.update(res, parsed=parsed, status=status, errors=errs)
                except Exception as e:  # network, auth, context too long, etc.
                    record.update(status="api_error", errors=[f"{type(e).__name__}: {e}"])
                record["seconds"] = round(time.time() - t0, 2)
                dest.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
                print(f"{mkey:18} {pid:12} run{run}  {record['status']}")


if __name__ == "__main__":
    main()

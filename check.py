"""Automatic checks on extraction runs, then a review sheet for people.

1. Quote check: is each source_passage really in the paper?
   exact      found after normalizing whitespace, quotes, dashes, ligatures
   near       not exact but a 90%+ fuzzy match (usually PDF hyphenation);
              a person should eyeball these
   not_found  the model made it up or changed it
2. Stability: groups the same scenario across the 3 runs and counts how
   many runs found it.
3. Writes review_sheet.xlsx. Model names are replaced with codes (A, B, C)
   so reviewers rate blind; the key goes in a separate file.

Remember: a quote passing this check only means the quote exists. Whether
it supports the scenario is the reviewers' call (see REVIEW_RUBRIC.md).

Usage:  python check.py --runs runs
"""
import argparse, json, random, re, unicodedata
from collections import defaultdict
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz

SAME_SCENARIO = 85  # similarity (0-100) above which two extractions count as one scenario


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"\[page \d+\]", " ", s)
    s = s.replace("­", "")                     # soft hyphen
    s = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", s)    # word- \n break
    s = re.sub(r"[‘’‚′]", "'", s)
    s = re.sub(r"[“”„″]", '"', s)
    s = re.sub(r"[‐-―−]", "-", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip().lower()


def quote_check(quote: str, paper_norm: str):
    q = norm(quote)
    if not q:
        return "not_found", 0
    if q in paper_norm:
        return "exact", 100
    score = fuzz.partial_ratio(q, paper_norm)
    return ("near" if score >= 90 else "not_found"), round(score)


def similarity(a: dict, b: dict) -> float:
    quote_sim = fuzz.token_set_ratio(norm(a.get("source_passage")), norm(b.get("source_passage")))
    summ_sim = fuzz.token_set_ratio(norm(a.get("summary")), norm(b.get("summary")))
    return max(quote_sim, summ_sim)


def cluster(items):
    """Greedy grouping. Items from the same run never merge with each other."""
    clusters = []
    for it in items:
        best, best_sim = None, 0
        for c in clusters:
            if it["run"] in {m["run"] for m in c}:
                continue
            sim = max(similarity(it["s"], m["s"]) for m in c)
            if sim > best_sim:
                best, best_sim = c, sim
        if best is not None and best_sim >= SAME_SCENARIO:
            best.append(it)
        else:
            clusters.append([it])
    return clusters


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="runs")
    ap.add_argument("--seed", type=int, default=20261015, help="seed for blinding codes")
    args = ap.parse_args()
    runs = Path(args.runs)

    records = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((runs / "raw").glob("*/*/run*.json"))]
    if not records:
        raise SystemExit("no runs found")

    models = sorted({r["model_key"] for r in records})
    codes = [chr(65 + i) for i in range(len(models))]
    random.Random(args.seed).shuffle(codes)
    blind = dict(zip(models, codes))

    texts = {}
    call_rows, scen_rows = [], []
    by_model_paper = defaultdict(list)

    for r in records:
        pid, mkey = r["paper_id"], r["model_key"]
        if pid not in texts:
            texts[pid] = norm((runs / "text" / f"{pid}.txt").read_text(encoding="utf-8"))
        parsed = r.get("parsed") if isinstance(r.get("parsed"), dict) else {}
        scens = (parsed.get("scenarios") or []) if r["status"] in ("ok", "schema_error", "truncated") else []
        call_rows.append({"model": mkey, "paper_id": pid, "run": r["run"], "status": r["status"],
                          "n_scenarios": len(scens), "errors": "; ".join(r.get("errors") or []),
                          "seconds": r.get("seconds"), "served_model": r.get("served_model"),
                          "served_provider": r.get("served_provider")})
        for s in scens:
            if isinstance(s, dict):
                by_model_paper[(mkey, pid)].append({"run": r["run"], "s": s})

    for (mkey, pid), items in sorted(by_model_paper.items()):
        n_runs = sum(1 for c in call_rows if c["model"] == mkey and c["paper_id"] == pid and c["status"] == "ok")
        for ci, cl in enumerate(cluster(items), start=1):
            rep = cl[0]["s"]
            qstat, qscore = quote_check(rep.get("source_passage", ""), texts[pid])
            runs_found = sorted({m["run"] for m in cl})
            scen_rows.append({
                "row_id": f"{blind[mkey]}-{pid}-{ci:02d}", "model_code": blind[mkey], "paper_id": pid,
                "found_in_runs": ",".join(map(str, runs_found)), "n_runs_found": len(runs_found),
                "stable": len(runs_found) >= 2, "ok_runs": n_runs,
                "quote_check": qstat, "quote_score": qscore,
                "summary": rep.get("summary"), "extraction_type": rep.get("extraction_type"),
                "causal_element": rep.get("causal_element"), "actor": rep.get("actor"),
                "ai_role": rep.get("ai_role"), "harm": rep.get("harm"),
                "source_passage": rep.get("source_passage"), "page_or_section": rep.get("page_or_section"),
                "justification": rep.get("justification"), "completeness": rep.get("completeness"),
                "completeness_note": rep.get("completeness_note"),
                # reviewer columns, left blank
                "reviewer": "", "support_rating": "", "elements_wrong": "", "duplicate_of": "", "notes": "",
            })

    scen = pd.DataFrame(scen_rows)
    calls = pd.DataFrame(call_rows)

    auto = []
    for mkey in models:
        c = calls[calls.model == mkey]
        s = scen[scen.model_code == blind[mkey]] if len(scen) else scen
        n = len(s)
        auto.append({
            "model_code": blind[mkey], "calls": len(c),
            "ok": int((c.status == "ok").sum()),
            "json_or_schema_errors": int(c.status.isin(["json_error", "schema_error"]).sum()),
            "refusals": int((c.status == "refusal").sum()),
            "truncated": int((c.status == "truncated").sum()),
            "api_errors": int((c.status == "api_error").sum()),
            "scenarios": n,
            "quote_exact_pct": round(100 * (s.quote_check == "exact").mean(), 1) if n else None,
            "quote_near_pct": round(100 * (s.quote_check == "near").mean(), 1) if n else None,
            "quote_not_found_pct": round(100 * (s.quote_check == "not_found").mean(), 1) if n else None,
            "stable_pct": round(100 * s.stable.mean(), 1) if n else None,
        })
    auto = pd.DataFrame(auto).sort_values("model_code")

    missed = pd.DataFrame(columns=["paper_id", "reviewer", "page_or_section", "summary", "why_it_qualifies",
                                   "missed_by_models (codes)"])

    out = runs / "review_sheet.xlsx"
    with pd.ExcelWriter(out, engine="openpyxl") as xw:
        scen.drop(columns=[]).to_excel(xw, sheet_name="scenarios", index=False)
        missed.to_excel(xw, sheet_name="missed", index=False)
        auto.to_excel(xw, sheet_name="auto_metrics", index=False)
        pd.DataFrame({"support_rating options": ["supported", "partially supported", "unsupported", "not a scenario"]}
                     ).to_excel(xw, sheet_name="rating_key", index=False)
        ws = xw.sheets["scenarios"]
        from openpyxl.worksheet.datavalidation import DataValidation
        col = scen.columns.get_loc("support_rating") + 1
        letter = ws.cell(row=1, column=col).column_letter
        dv = DataValidation(type="list", formula1='"supported,partially supported,unsupported,not a scenario"',
                            allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(f"{letter}2:{letter}{len(scen) + 1}")
        ws.freeze_panes = "B2"

    (runs / "blinding_key.json").write_text(json.dumps(blind, indent=2))
    calls.to_csv(runs / "call_log.csv", index=False)
    print(auto.to_string(index=False))
    print(f"\nreview sheet: {out}\nblinding key (don't share with reviewers): {runs / 'blinding_key.json'}")


if __name__ == "__main__":
    main()

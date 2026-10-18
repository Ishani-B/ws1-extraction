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
    ap.add_argument("--reviewers", default="reviewer1,reviewer2",
                    help="comma-separated names; one independent sheet each")
    args = ap.parse_args()
    runs = Path(args.runs)

    records = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((runs / "raw").glob("*/*/run*.json"))]
    if not records:
        raise SystemExit("no runs found")

    # codes are assigned over every model in the run's config, so they stay the same
    # while some models are still running
    import yaml
    cfg_used = yaml.safe_load((runs / "config_used.yaml").read_text())
    all_models = sorted(cfg_used["models"])
    runs_per = cfg_used["runs_per_paper"]
    models = all_models
    codes = [chr(65 + i) for i in range(len(all_models))]
    random.Random(args.seed).shuffle(codes)
    blind = dict(zip(all_models, codes))
    key_file = runs / "blinding_key.json"
    if key_file.exists():  # never reshuffle once reviewers have seen codes
        blind = json.loads(key_file.read_text())

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
                          "served_provider": r.get("served_provider"),
                          "pinned_provider": ",".join((r.get("provider_pin") or {}).get("order") or []),
                          "prompt_tokens": (r.get("usage") or {}).get("prompt_tokens"),
                          "completion_tokens": (r.get("usage") or {}).get("completion_tokens"),
                          "cost_usd": (r.get("usage") or {}).get("cost"),
                          "retries": max(0, len(r.get("attempts") or []) - 1)})
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
            "wrong_host_calls": int(((c.served_provider.fillna("") != c.pinned_provider) & (c.status == "ok")
                                     & (c.pinned_provider != "")).sum()),
            "median_seconds": c.seconds.median(),
            "output_tokens_total": int(c.completion_tokens.fillna(0).sum()),
            "cost_usd_total": round(float(pd.to_numeric(c.cost_usd, errors="coerce").fillna(0).sum()), 3),
        })
    auto = pd.DataFrame(auto).sort_values("model_code")

    # ---- which (model, paper) pairs are finished ----
    papers = sorted({r["paper_id"] for r in records})
    status_rows = []
    for mkey in all_models:
        for pid in papers:
            c = calls[(calls.model == mkey) & (calls.paper_id == pid)] if len(calls) else calls
            done = len(c) >= runs_per and not (c.status == "api_error").any()
            status_rows.append({"model_code": blind[mkey], "paper_id": pid, "runs_done": len(c),
                                "ready_to_review": "yes" if done else "not yet"})
            if not done and len(scen):
                scen = scen[~((scen.model_code == blind[mkey]) & (scen.paper_id == pid))]
    status = pd.DataFrame(status_rows).sort_values(["paper_id", "model_code"])

    # ---- reviewer sheets: scenarios + ratings only, nothing that identifies a model ----
    show = ["row_id", "paper_id", "model_code", "quote_check", "summary", "source_passage", "page_or_section",
            "extraction_type", "completeness", "completeness_note", "actor", "ai_role", "harm", "causal_element",
            "justification", "found_in_runs"]
    rate = ["support_rating", "elements_wrong", "duplicate_of", "notes"]
    sheet = scen[show].copy() if len(scen) else pd.DataFrame(columns=show)
    for c in rate:
        sheet[c] = ""
    sheet = sheet.sort_values(["paper_id", "model_code", "row_id"])
    missed_cols = ["paper_id", "page_or_section", "summary", "why_it_qualifies", "missed_by (model codes)"]
    key_rows = pd.DataFrame({
        "column": ["support_rating", "", "", "", "elements_wrong", "duplicate_of", "quote_check", "", "",
                   "found_in_runs", "missed tab"],
        "meaning": ["supported: everything in the summary is in the paper",
                    "partially supported: core is there, one element added or overstated (say which in elements_wrong)",
                    "unsupported: quote is real but the paper doesn't say or argue this scenario",
                    "not a scenario: fails the definition (no causal link, no AI, or no bio harm)",
                    "actor / AI role / harm / causal link / extraction type / completeness",
                    "row_id of the row this repeats",
                    "exact: quote found in the paper; near: almost (usually PDF hyphenation), check it;",
                    "not_found: quote isn't in the paper. It only shows the quote exists, not that it supports the scenario",
                    "",
                    "which of the 3 runs found it; 1 run only = less stable",
                    "after rating a paper, log scenarios no model found, one per row"]})

    def write_sheet(path):
        old_rate, old_missed = {}, None
        if path.exists():  # keep ratings already entered
            prev = pd.read_excel(path, sheet_name=None, dtype=str)
            if "scenarios" in prev:
                for _, r in prev["scenarios"].fillna("").iterrows():
                    old_rate[r["row_id"]] = {c: r.get(c, "") for c in rate}
            old_missed = prev.get("missed")
        s = sheet.copy()
        for i, r in s.iterrows():
            for c, v in old_rate.get(r["row_id"], {}).items():
                s.at[i, c] = v
        missed = old_missed if old_missed is not None else pd.DataFrame(columns=missed_cols)
        with pd.ExcelWriter(path, engine="openpyxl") as xw:
            s.to_excel(xw, sheet_name="scenarios", index=False)
            missed.to_excel(xw, sheet_name="missed", index=False)
            status.to_excel(xw, sheet_name="progress", index=False)
            key_rows.to_excel(xw, sheet_name="how_to_rate", index=False)
            from openpyxl.worksheet.datavalidation import DataValidation
            from openpyxl.styles import Alignment, Font, PatternFill
            ws = xw.sheets["scenarios"]
            col = s.columns.get_loc("support_rating") + 1
            letter = ws.cell(row=1, column=col).column_letter
            dv = DataValidation(type="list", formula1='"supported,partially supported,unsupported,not a scenario"',
                                allow_blank=True)
            ws.add_data_validation(dv)
            dv.add(f"{letter}2:{letter}{max(2, len(s) + 1)}")
            widths = {"row_id": 14, "paper_id": 8, "model_code": 7, "quote_check": 10, "summary": 50,
                      "source_passage": 60, "page_or_section": 12, "justification": 40, "notes": 30}
            for j, name in enumerate(s.columns, start=1):
                L = ws.cell(row=1, column=j).column_letter
                ws.column_dimensions[L].width = widths.get(name, 14)
                ws.cell(row=1, column=j).font = Font(bold=True)
                if name in rate:
                    ws.cell(row=1, column=j).fill = PatternFill("solid", fgColor="FFFF00")
                for i in range(2, len(s) + 2):
                    ws.cell(row=i, column=j).alignment = Alignment(wrap_text=name in ("summary", "source_passage",
                                                                                       "justification", "notes"),
                                                                    vertical="top")
            ws.freeze_panes = "D2"
            for name, w in (("missed", [8, 14, 60, 40, 18]), ("how_to_rate", [16, 100]), ("progress", [10, 8, 10, 14])):
                for j, width in enumerate(w, start=1):
                    xw.sheets[name].column_dimensions[xw.sheets[name].cell(row=1, column=j).column_letter].width = width
        return sum(1 for r in old_rate.values() if r.get("support_rating"))

    written = []
    for name in args.reviewers.split(","):
        p = runs / f"review_{name.strip()}.xlsx"
        kept = write_sheet(p)
        written.append(f"{p.name} ({kept} existing ratings kept)")

    # ---- analyst-only file: numbers per model (still coded) + full call log ----
    with pd.ExcelWriter(runs / "pilot_metrics.xlsx", engine="openpyxl") as xw:
        auto.to_excel(xw, sheet_name="auto_metrics", index=False)
        calls.drop(columns=["model"]).assign(model_code=calls.model.map(blind)).to_excel(xw, sheet_name="call_log", index=False)
        status.to_excel(xw, sheet_name="progress", index=False)
    (runs / "blinding_key.json").write_text(json.dumps(blind, indent=2))
    calls.to_csv(runs / "call_log.csv", index=False)
    print(auto.to_string(index=False))
    print(f"\nreviewer sheets: {', '.join(written)}")
    print(f"rows ready to review: {len(sheet)}; pending model/paper pairs: {int((status.ready_to_review != 'yes').sum())}")
    print(f"analyst file: {runs / 'pilot_metrics.xlsx'}   blinding key (don't share): {runs / 'blinding_key.json'}")


if __name__ == "__main__":
    main()

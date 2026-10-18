"""Pick the pilot papers before the full validation sample is drawn.

Draws from corpus papers that already have a PDF and a settled source_type
(skips needs_check), one per source-type group so the pilot covers different
kinds of documents, then fills any remaining slots at random from the two big
groups (peer-reviewed, preprint). Small types (report, government, red-team)
are pooled into one group and contribute exactly one paper, so the pilot
doesn't drain a small stratum the validation sample needs.

Writes samples/pilot_ids.txt and samples/pilot_record.json. Pass the ids file
to sample.py with --exclude later, so the pilot papers never end up in the
14-paper validation sample.

Usage:  python3 pick_pilot.py --seed <number>
"""
import argparse, csv, json, random
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GROUP = {"peer-reviewed": "peer-reviewed", "preprint": "preprint",
         "institutional report": "report / government / red-team",
         "government": "report / government / red-team",
         "red-team study": "report / government / red-team",
         "AI lab evaluation": "report / government / red-team"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--corpus", default=str(ROOT / "data/corpus.csv"))
    ap.add_argument("--out", default=str(ROOT / "samples"))
    args = ap.parse_args()

    rows = [r for r in csv.DictReader(open(args.corpus, encoding="utf-8"))
            if r["source_type"] in GROUP and r.get("file_path") and Path(r["file_path"]).exists()]
    groups = defaultdict(list)
    for r in rows:
        groups[GROUP[r["source_type"]]].append(r["paper_id"])
    for g in groups:
        groups[g].sort()

    rng = random.Random(args.seed)
    pick = [rng.choice(groups[g]) for g in sorted(groups)]
    # extra slots come only from the big groups, so small strata keep their papers for validation
    big = {"peer-reviewed", "preprint"}
    pool = sorted(p for g in big for p in groups[g] if p not in pick)
    pick += rng.sample(pool, args.n - len(pick))
    pick.sort()

    out = Path(args.out)
    out.mkdir(exist_ok=True)
    (out / "pilot_ids.txt").write_text("\n".join(pick) + "\n")
    by_id = {r["paper_id"]: r for r in rows}
    rec = {"seed": args.seed, "eligible": len(rows),
           "eligible_by_group": {g: len(v) for g, v in sorted(groups.items())},
           "pilot": [{"paper_id": p, "source_type": by_id[p]["source_type"], "title": by_id[p]["title"]} for p in pick]}
    (out / "pilot_record.json").write_text(json.dumps(rec, indent=2))
    print(f"{len(rows)} eligible papers; seed {args.seed}")
    for p in rec["pilot"]:
        print(f"  {p['paper_id']}  {p['source_type']:20} {p['title'][:70]}")


if __name__ == "__main__":
    main()

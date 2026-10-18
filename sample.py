"""Draw the 14-paper human-coded validation sample and the 5-paper pilot set.

Validation sample: 10% of the corpus, stratified by source_type in
proportion to each type's share, rounded with the largest-remainder rule so
the total is exact. Random draw within each type using a fixed seed.

Pilot set: drawn from the papers NOT in the validation sample, one per
source type first so each type is represented, then filled at random.

Same corpus + same seed always gives the same draw. Record the seed in the
protocol.

Usage:  python sample.py --corpus corpus.csv --seed 20261015
"""
import argparse, csv, json, math, random
from collections import defaultdict
from pathlib import Path


def allocate(counts: dict, total: int) -> dict:
    """Largest-remainder rounding: proportional shares that sum to exactly `total`."""
    n = sum(counts.values())
    raw = {k: total * v / n for k, v in counts.items()}
    alloc = {k: math.floor(x) for k, x in raw.items()}
    left = total - sum(alloc.values())
    # ties broken by stratum name so the result never depends on dict order
    for k in sorted(raw, key=lambda k: (-(raw[k] - alloc[k]), k))[:left]:
        alloc[k] += 1
    return alloc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True, help="CSV with paper_id and source_type columns")
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--fraction", type=float, default=0.10)
    ap.add_argument("--pilot", type=int, default=5)
    ap.add_argument("--out", default="samples")
    ap.add_argument("--exclude", help="file of paper_ids to keep out of the draw (e.g. pilot papers chosen earlier)")
    args = ap.parse_args()

    with open(args.corpus, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    ids = [r["paper_id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise SystemExit("duplicate paper_ids in corpus")
    excluded = set()
    if args.exclude:
        excluded = {l.strip() for l in Path(args.exclude).read_text().splitlines() if l.strip()}
        rows = [r for r in rows if r["paper_id"] not in excluded]
        print(f"Excluding {len(excluded)} papers listed in {args.exclude}")

    unlabeled = [r["paper_id"] for r in rows if r["source_type"].strip() in ("", "needs_check")]
    if unlabeled:
        raise SystemExit(f"{len(unlabeled)} papers have no source_type yet (e.g. {unlabeled[:5]}). "
                         "Label them before drawing, or the strata will be wrong.")

    strata = defaultdict(list)
    for r in rows:
        strata[r["source_type"].strip()].append(r["paper_id"])
    for k in strata:
        strata[k].sort()  # draw depends only on the ids, not the CSV row order

    # 10% of the full corpus, including any excluded papers
    n_val = round((len(rows) + len(excluded)) * args.fraction)
    alloc = allocate({k: len(v) for k, v in strata.items()}, n_val)

    rng = random.Random(args.seed)
    validation = {}
    for k in sorted(strata):
        validation[k] = sorted(rng.sample(strata[k], alloc[k]))
    val_ids = {p for v in validation.values() for p in v}

    remaining = {k: [p for p in v if p not in val_ids] for k, v in strata.items()}
    pilot = []
    for k in sorted(remaining):
        if remaining[k] and len(pilot) < args.pilot:
            pilot.append(rng.choice(remaining[k]))
    pool = sorted(p for v in remaining.values() for p in v if p not in pilot)
    pilot += rng.sample(pool, args.pilot - len(pilot))
    pilot.sort()

    out = Path(args.out)
    out.mkdir(exist_ok=True)
    (out / "validation_ids.txt").write_text("\n".join(sorted(val_ids)) + "\n")
    if args.pilot > 0:  # never overwrite a pilot list chosen earlier (pick_pilot.py)
        (out / "pilot_ids.txt").write_text("\n".join(pilot) + "\n")
    record = {
        "seed": args.seed, "corpus_size": len(rows) + len(excluded), "excluded": sorted(excluded),
        "fraction": args.fraction,
        "validation_size": n_val,
        "strata": {k: {"in_corpus": len(strata[k]),
                       "exact_share": round(n_val * len(strata[k]) / len(rows), 3),
                       "sampled": alloc[k], "ids": validation[k]} for k in sorted(strata)},
        "pilot_ids": pilot,
    }
    (out / "sampling_record.json").write_text(json.dumps(record, indent=2))

    print(f"Validation sample: {n_val} of {len(rows)} papers (seed {args.seed})")
    for k in sorted(strata):
        print(f"  {k:30} {len(strata[k]):4} in corpus  ->  {alloc[k]} sampled")
    print(f"Pilot (outside validation sample): {', '.join(pilot) if pilot else 'not drawn here'}")
    print(f"Wrote {out}/validation_ids.txt, pilot_ids.txt, sampling_record.json")


if __name__ == "__main__":
    main()

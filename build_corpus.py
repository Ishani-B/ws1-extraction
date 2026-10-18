"""Build corpus.csv (the input to sample.py and extract.py) from the Rayyan export.

Inputs
  --ris        Rayyan RIS export (screening decisions live in the N1 field)
  --screening  screening_set.csv, used to map records to R#### ids and record type

Outputs (in --out)
  corpus.csv           included records: paper_id, source_type, file_path, + metadata
  conflicts.csv        records where the two screeners disagree; not in corpus until resolved
  source_type_log.csv  which rule assigned each source_type, so it can be audited and overridden

Inclusion rule: Ishani voted Include and no screener voted Exclude.
Conflicts (one Include, one Exclude) are held out until resolved.

source_type uses the charting-form categories. It is assigned by fixed rules
from the record type and venue, so the same input always gives the same
labels. Anything a rule can't place is marked needs_check.
"""
import argparse, csv, re
from collections import Counter
from pathlib import Path


def parse_ris(path):
    recs, cur, last = [], {}, None
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        m = re.match(r"^([A-Z][A-Z0-9])  - ?(.*)$", line)
        if m:
            tag, val = m.groups()
            last = tag
            if tag == "ER":
                recs.append(cur); cur = {}
                continue
            cur.setdefault(tag, []).append(val)
        elif line.strip() and last and cur.get(last):
            cur[last][-1] += " " + line.strip()
    return recs


def decisions(rec):
    n1 = " | ".join(rec.get("N1", []))
    m = re.search(r"RAYYAN-INCLUSION: \{([^}]*)\}", n1)
    return {k.strip(): v for k, v in re.findall(r'"([^"]+)"=>"([^"]+)"', m.group(1))} if m else {}


def key(title):
    return re.sub(r"[^a-z0-9]", "", (title or "").lower())[:120]


PREPRINT_VENUES = r"arxiv|biorxiv|medrxiv|zenodo|ssrn|preprints\.org|research square"
INSTITUTION_VENUES = r"rand corporation|rand health quarterly|belfer center|nato science|kitopen|studies in peace and security"
GOV_TITLES = r"managing misuse risk for dual-use foundation models|how the uk government should"
REPORT_TITLES = r"understanding ai-facilitated biological weapon|responsible biodesign workshop|ai-enabled biological design and the risks"


def source_type(title, venue, rtype):
    t, v, ty = title.lower(), venue.lower(), rtype.lower()
    if re.search(r"red[- ]team", t):
        return "red-team study", "title mentions red-team"
    if re.search(GOV_TITLES, t):
        return "government", "known government publication (title)"
    if re.search(INSTITUTION_VENUES, v) or re.search(REPORT_TITLES, t) or ty == "report":
        return "institutional report", "think tank / institutional venue or report type"
    if re.search(PREPRINT_VENUES, v) or "preprint" in ty:
        return "preprint", "preprint server venue or preprint type"
    # categories added by team decision 17 Oct 2026: book chapter, miscellaneous
    if ty in ("dissertation", "thes"):
        return "miscellaneous", "thesis"
    if "book" in ty or "chapter" in ty or ty in ("reference-entry", "other"):
        return "book chapter", "book, chapter, reference entry or 'other' record type"
    if ty in ("journal article", "article", "conference paper", "conference-paper", "editorial", "letter",
              "review", "jour", "cpaper"):
        if not v:
            return "miscellaneous", "article type but no venue in the record"
        return "peer-reviewed", "journal or conference venue"
    return "needs_check", f"unmapped record type '{rtype}'"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ris", required=True)
    ap.add_argument("--screening", required=True)
    ap.add_argument("--out", default=".")
    ap.add_argument("--lead", default="Ishani", help="first screener's name in Rayyan")
    args = ap.parse_args()

    screen = {}
    for r in csv.DictReader(open(args.screening, encoding="utf-8")):
        screen.setdefault(key(r["title"]), r)

    included, conflicts, unmatched = [], [], []
    for rec in parse_ris(args.ris):
        d = decisions(rec)
        votes = set(d.values())
        if "Included" not in votes:
            continue
        g = lambda t: (rec.get(t) or [""])[0].strip()
        title = g("TI")
        s = screen.get(key(title))
        if s is None:
            unmatched.append(title)
        row = {
            "paper_id": s["rec"] if s else g("AN"),
            "title": title, "year": g("Y1"), "venue": g("T2") or (s or {}).get("venue", ""),
            "record_type": (s or {}).get("type", g("TY")),
            "doi": g("DO") or (s or {}).get("doi", ""), "url": g("UR") or (s or {}).get("url", ""),
            "rayyan_id": g("AN"),
            "votes": "; ".join(f"{k}: {v}" for k, v in sorted(d.items())),
        }
        st, rule = source_type(row["title"], row["venue"], row["record_type"])
        row.update(source_type=st, source_type_rule=rule, file_path="")
        if d.get(args.lead) == "Included" and "Excluded" not in votes:
            included.append(row)
        else:
            conflicts.append(row)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cols = ["paper_id", "source_type", "file_path", "title", "year", "venue", "record_type",
            "doi", "url", "rayyan_id", "votes"]
    for name, data in (("corpus.csv", included), ("conflicts.csv", conflicts)):
        with open(out / name, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(sorted(data, key=lambda r: r["paper_id"]))
    with open(out / "source_type_log.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["paper_id", "source_type", "source_type_rule", "record_type", "venue", "title"],
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(included + conflicts, key=lambda r: r["paper_id"]))

    print(f"included: {len(included)}   conflicts held out: {len(conflicts)}   unmatched to R####: {len(unmatched)}")
    print("source types (included):", Counter(r["source_type"] for r in included).most_common())
    for t in unmatched:
        print("  no R#### match:", t[:90])


if __name__ == "__main__":
    main()

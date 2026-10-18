"""Check each downloaded PDF is actually the paper it's filed under.

Compares the record's title with the text of the PDF's first three pages.
  match     title found (fuzzy) on the first pages
  check     weak match; open it and look
  mismatch  title not found; probably the wrong document
  no_text   no text layer (scanned PDF): needs OCR before extraction

Usage:  python3 verify_pdfs.py   (needs: pip install pymupdf)
Writes data/pdf_check.csv.
"""
import argparse, csv, re
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def norm(s):
    s = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", s or "")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s.lower())).strip()


def title_score(title, text):
    t, x = norm(title), norm(text)
    if not t:
        return 0.0
    if t in x:
        return 1.0
    words = t.split()
    # best fuzzy match of the title against same-length windows of the text
    best, n = 0.0, len(words)
    xw = x.split()
    for i in range(0, max(1, len(xw) - n + 1)):
        r = SequenceMatcher(None, t, " ".join(xw[i:i + n])).ratio()
        best = max(best, r)
        if best > 0.95:
            break
    return best


def main():
    import pymupdf
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", nargs="+", default=[str(ROOT / "data/corpus.csv"), str(ROOT / "data/conflicts.csv")])
    ap.add_argument("--pdf-dir", default=str(ROOT / "pdfs"))
    args = ap.parse_args()
    out = []
    for p in args.csv:
        for row in csv.DictReader(open(p, encoding="utf-8")):
            f = Path(args.pdf_dir) / f"{row['paper_id']}.pdf"
            if not f.exists():
                continue
            with pymupdf.open(f) as doc:
                pages = doc.page_count
                text = " ".join(doc[i].get_text() for i in range(min(3, pages)))
                total_chars = sum(len(pg.get_text()) for pg in doc)
            if total_chars < 500:
                status, score = "no_text", 0.0
            else:
                score = title_score(row["title"].rstrip("."), text)
                status = "match" if score >= 0.85 else "check" if score >= 0.6 else "mismatch"
            out.append({"paper_id": row["paper_id"], "status": status, "title_score": round(score, 2),
                        "pages": pages, "chars": total_chars, "title": row["title"],
                        "first_text": re.sub(r"\s+", " ", text)[:200]})
    with open(ROOT / "data/pdf_check.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    from collections import Counter
    print(Counter(r["status"] for r in out))
    for r in out:
        if r["status"] != "match":
            print(f"{r['paper_id']:8} {r['status']:8} {r['title_score']:.2f} {r['pages']:4}p  {r['title'][:55]} || {r['first_text'][:70]}")


if __name__ == "__main__":
    main()

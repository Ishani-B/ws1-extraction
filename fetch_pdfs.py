"""Download open-access PDFs for the corpus and list the ones that need the library.

Standard library only (no installs). Run from the ws1-extraction folder:

  python3 fetch_pdfs.py                      # corpus.csv + conflicts.csv
  python3 fetch_pdfs.py --email you@x.edu    # also asks Unpaywall (needs an email)

For each paper it tries, in order:
  1. arXiv, if the record is an arXiv paper
  2. OpenAlex: the open-access PDF links it knows about (looked up by DOI,
     or by title when there's no DOI; title matches must be 92%+ similar)
  3. Unpaywall, if --email is given
  4. The landing pages (record URL, DOI link, OpenAlex landing pages):
     reads the citation_pdf_url tag publishers include, or a direct .pdf link

A download only counts if the file really is a PDF. Saved as pdfs/<paper_id>.pdf,
and file_path is filled in in corpus.csv / conflicts.csv. Already-downloaded
papers are skipped, so it's safe to re-run.

Outputs:
  data/fetch_log.csv      what was tried for every paper and what worked
  data/needs_manual.csv   papers with no open copy found: get these through the library,
                          save as pdfs/<paper_id>.pdf, and re-run to pick them up
"""
import argparse, csv, difflib, html, json, re, sys, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
UA = "WS1-scenario-review/1.0 (academic literature review; polite single-threaded fetch)"
DELAY = 1.0


def get(url, accept="*/*", timeout=40):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.geturl(), r.headers.get("Content-Type", "")


def get_json(url):
    body, _, _ = get(url, accept="application/json")
    return json.loads(body)


def is_pdf(b):
    return b[:1024].lstrip().startswith(b"%PDF") and len(b) > 10_000


def norm_title(t):
    return re.sub(r"[^a-z0-9 ]", "", (t or "").lower()).strip()


def arxiv_id(row):
    for s in (row.get("url", ""), row.get("doi", "")):
        m = re.search(r"arxiv\.org/(?:abs|pdf)/([\w.\-/]+?)(?:v\d+)?(?:\.pdf)?$", s, re.I) or \
            re.search(r"10\.48550/arxiv\.([\w.\-]+)", s, re.I)
        if m:
            return m.group(1)
    return None


def clean_doi(d):
    d = (d or "").strip()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d, flags=re.I)
    return d if d.startswith("10.") else ""


def openalex(row, mailto):
    q = f"&mailto={urllib.parse.quote(mailto)}" if mailto else ""
    doi = clean_doi(row.get("doi"))
    work = None
    if doi:
        try:
            work = get_json(f"https://api.openalex.org/works/doi:{urllib.parse.quote(doi)}?{q[1:]}")
        except urllib.error.HTTPError:
            work = None
    if work is None and row.get("title"):
        t = row["title"].rstrip(".")
        res = get_json("https://api.openalex.org/works?per_page=3&search=" + urllib.parse.quote(t[:200]) + q)
        for w in res.get("results", []):
            if difflib.SequenceMatcher(None, norm_title(w.get("title")), norm_title(t)).ratio() >= 0.92:
                work = w
                break
    if not work:
        return [], []
    pdfs, pages = [], []
    locs = [work.get("best_oa_location")] + (work.get("locations") or [])
    for loc in locs:
        if not loc:
            continue
        if loc.get("pdf_url"):
            pdfs.append(loc["pdf_url"])
        if loc.get("landing_page_url"):
            pages.append(loc["landing_page_url"])
    oa = (work.get("open_access") or {}).get("oa_url")
    if oa:
        pdfs.append(oa)
    return pdfs, pages


def unpaywall(row, email):
    doi = clean_doi(row.get("doi"))
    if not (doi and email):
        return []
    try:
        d = get_json(f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}?email={urllib.parse.quote(email)}")
    except urllib.error.HTTPError:
        return []
    out = []
    for loc in [d.get("best_oa_location")] + (d.get("oa_locations") or []):
        if loc and loc.get("url_for_pdf"):
            out.append(loc["url_for_pdf"])
    return out


def pdf_links_on_page(url):
    """Fetch a landing page; return (pdf bytes if the page itself is a PDF, candidate PDF links)."""
    body, final, ctype = get(url, accept="text/html,application/pdf")
    if is_pdf(body):
        return body, []
    text = body.decode("utf-8", "ignore")
    cands = re.findall(r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)', text, re.I)
    cands += re.findall(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']citation_pdf_url', text, re.I)
    cands += [h for h in re.findall(r'href=["\']([^"\']+\.pdf(?:\?[^"\']*)?)["\']', text, re.I)
              if not re.search(r"supplement|appendix|brochure|annual-report", h, re.I)][:3]
    return None, [urllib.parse.urljoin(final, html.unescape(c)) for c in cands]


REJECTED = set()  # URLs verify_pdfs.py found to be the wrong document


def try_pdf(url, tried):
    if url in tried or url in REJECTED:
        return None
    tried.append(url)
    try:
        body, _, _ = get(url, accept="application/pdf,*/*")
        time.sleep(DELAY)
        return body if is_pdf(body) else None
    except Exception:
        time.sleep(DELAY)
        return None


def fetch_one(row, email):
    tried, notes = [], []
    aid = arxiv_id(row)
    if aid:
        b = try_pdf(f"https://arxiv.org/pdf/{aid}", tried)
        if b:
            return b, "arxiv", tried[-1], tried
    try:
        oa_pdfs, oa_pages = openalex(row, email)
        time.sleep(DELAY)
    except Exception as e:
        oa_pdfs, oa_pages = [], []
        notes.append(f"openalex error {type(e).__name__}")
    for u in oa_pdfs:
        b = try_pdf(u, tried)
        if b:
            return b, "openalex", u, tried
    for u in unpaywall(row, email):
        b = try_pdf(u, tried)
        if b:
            return b, "unpaywall", u, tried
    pages = [row.get("url", "")] + ([f"https://doi.org/{clean_doi(row['doi'])}"] if clean_doi(row.get("doi")) else []) + oa_pages
    seen = set()
    for p in pages:
        if not p or p in seen or "pubmed.ncbi.nlm.nih.gov" in p or "scopus.com" in p:
            continue
        seen.add(p)
        try:
            direct, links = pdf_links_on_page(p)
            time.sleep(DELAY)
        except Exception:
            time.sleep(DELAY)
            continue
        if direct and p not in REJECTED:
            return direct, "landing page", p, tried + [p]
        for u in links:
            b = try_pdf(u, tried)
            if b:
                return b, "landing page link", u, tried
    return None, "; ".join(notes) or "no open copy found", "", tried


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", nargs="+", default=[str(ROOT / "data/corpus.csv"), str(ROOT / "data/conflicts.csv")])
    ap.add_argument("--pdf-dir", default=str(ROOT / "pdfs"))
    ap.add_argument("--email", default="", help="your email, for Unpaywall and OpenAlex's polite pool")
    ap.add_argument("--limit", type=int, default=0, help="only try this many papers (for a test run)")
    ap.add_argument("--ids", nargs="*", default=None, help="only try these paper_ids")
    args = ap.parse_args()

    rej = ROOT / "data/rejected_urls.txt"
    if rej.exists():
        REJECTED.update(l.split("\t")[-1].strip() for l in rej.read_text().splitlines() if l.strip())

    pdf_dir = Path(args.pdf_dir)
    pdf_dir.mkdir(parents=True, exist_ok=True)
    log_path = ROOT / "data/fetch_log.csv"
    log = {r["paper_id"]: r for r in csv.DictReader(open(log_path, encoding="utf-8"))} if log_path.exists() else {}

    tables = {}
    for p in args.csv:
        with open(p, newline="", encoding="utf-8") as f:
            rd = csv.DictReader(f)
            tables[p] = (rd.fieldnames, list(rd))

    n_try = 0
    for p, (cols, rows) in tables.items():
        for row in rows:
            pid = row["paper_id"]
            dest = pdf_dir / f"{pid}.pdf"
            if dest.exists() and is_pdf(dest.read_bytes()):
                row["file_path"] = str(dest)
                if log.get(pid, {}).get("status") != "ok":
                    log[pid] = {"paper_id": pid, "status": "ok", "source": "already on disk", "pdf_url": "",
                                "tried": "", "title": row["title"]}
                continue
            row["file_path"] = ""  # no valid PDF on disk (e.g. moved to _rejected)
            if args.limit and n_try >= args.limit:
                continue
            if args.ids is not None and pid not in args.ids:
                continue
            n_try += 1
            b, source, url, tried = fetch_one(row, args.email)
            if b:
                dest.write_bytes(b)
                row["file_path"] = str(dest)
                status = "ok"
            else:
                status = "manual"
            log[pid] = {"paper_id": pid, "status": status, "source": source, "pdf_url": url,
                        "tried": " | ".join(tried)[:2000], "title": row["title"]}
            print(f"{pid:8} {status:7} {source[:22]:22} {row['title'][:60]}", flush=True)

        with open(p, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)

    with open(log_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["paper_id", "status", "source", "pdf_url", "tried", "title"])
        w.writeheader()
        w.writerows(sorted(log.values(), key=lambda r: r["paper_id"]))

    manual = []
    for p, (cols, rows) in tables.items():
        for row in rows:
            if not row.get("file_path"):
                manual.append({"paper_id": row["paper_id"], "list": Path(p).stem, "title": row["title"],
                               "year": row.get("year", ""), "venue": row.get("venue", ""),
                               "doi": row.get("doi", ""), "url": row.get("url", "")})
    with open(ROOT / "data/needs_manual.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["paper_id", "list", "title", "year", "venue", "doi", "url"])
        w.writeheader()
        w.writerows(manual)

    total = sum(len(r) for _, r in tables.values())
    print(f"\n{total - len(manual)} of {total} papers have a PDF. {len(manual)} listed in data/needs_manual.csv.")


if __name__ == "__main__":
    main()

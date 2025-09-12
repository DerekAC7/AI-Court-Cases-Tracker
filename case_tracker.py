import os
import re
import json
import hashlib
import requests
from bs4 import BeautifulSoup

# ========= CONFIG =========
REPO_OWNER = "DerekAC7"
REPO_NAME  = "AI-Court-Cases-Tracker"
TOKEN = os.getenv("PERSONAL_ACCESS_TOKEN")  # mapped from secrets.PAT_TOKEN in the workflow
COMMON_LABELS = ["AI Training", "Court Case"]
SOURCE_LABEL_PREFIX = "Source: "
TIMEOUT = 30
DOCS_DIR = "docs"
JSON_PATH = f"{DOCS_DIR}/cases.json"
# =========================

API_BASE = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}"

# --------- CASE DETECTION (tighten to avoid junk) ----------
CASE_PATTERNS = [
    r"\b[A-Z][\w'.-]{1,40}\s+v\.\s+[A-Z][\w'.-]{1,40}\b",  # Foo v. Bar
    r"\b[A-Z][\w'.-]{1,40}\s+vs\.?\s+[A-Z][\w'.-]{1,40}\b", # Foo vs. Bar
    r"\bIn\s+re\s+[A-Z][\w'.-]+",                           # In re Something
    r"\bU\.S\.\s+v\.\s+[A-Z][\w'.-]{1,40}\b",               # U.S. v. X
]
CASE_REGEX = re.compile("|".join(CASE_PATTERNS))

# Common “non-case” noise we’ll drop if detected in titles
JUNK_TITLE_PHRASES = [
    "litigation tracker", "case updates", "current edition", "disclaimer",
    "artificial intelligence (ai)", "training & development",
    "copyright",
    "interactive entertainment", "retail", "philanthropic", "blockchain",
    "mdr mayfair", "metaverse", "nfts", "cryptoassets"
]

# ---------- GitHub helpers ----------
def gh_headers():
    if not TOKEN:
        raise RuntimeError("Missing token. In the workflow, map secrets.PAT_TOKEN to PERSONAL_ACCESS_TOKEN.")
    return {
        "Authorization": f"token {TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "ai-litigation-tracker-bot"
    }

def ensure_labels(extra_labels):
    r = requests.get(f"{API_BASE}/labels?per_page=100", headers=gh_headers(), timeout=TIMEOUT)
    r.raise_for_status()
    have = {x["name"] for x in r.json()}
    for name in set(COMMON_LABELS) | set(extra_labels):
        if name not in have:
            requests.post(f"{API_BASE}/labels", headers=gh_headers(), json={"name": name}, timeout=TIMEOUT)

def list_existing_issue_keys():
    keys = set()
    for state in ("open", "closed"):
        page = 1
        while True:
            r = requests.get(
                f"{API_BASE}/issues", headers=gh_headers(),
                params={"state": state, "per_page": 100, "page": page}, timeout=TIMEOUT
            )
            r.raise_for_status()
            items = r.json()
            if not items: break
            for it in items:
                if "pull_request" in it:  # skip PRs
                    continue
                body = it.get("body") or ""
                m = re.search(r"<!--\s*KEY:\s*([a-f0-9]{32})\s*-->", body, flags=re.I)
                if m:
                    keys.add(m.group(1))
            page += 1
    return keys

def create_issue(title, body, labels):
    payload = {"title": title, "body": body, "labels": labels}
    r = requests.post(f"{API_BASE}/issues", headers=gh_headers(), json=payload, timeout=TIMEOUT)
    if r.status_code != 201:
        raise RuntimeError(f"Issue create failed: {r.status_code} {r.text}")
    print(f"Created: {title}")

# ---------- utils ----------
def get_html(url):
    return requests.get(url, timeout=TIMEOUT).text

def clean(text):
    return re.sub(r"\s+", " ", (text or "").strip())

def is_probable_case(title):
    t = title.lower()
    if any(p in t for p in JUNK_TITLE_PHRASES):
        return False
    return bool(CASE_REGEX.search(title))

def infer_outcome_short(text):
    m = re.search(
        r"(fair use|summary judgment|partial summary judgment|dismiss(ed)?|prelim(inary)? injunction|injunction|"
        r"settle(d)?|class action|certification|md(l)?|transfer|stay(ed)?|remand|contempt|damages)",
        text, re.I
    )
    return clean(m.group(0)).capitalize() if m else "Update"

def normalize_case_key(title):
    t = title.lower()
    t = re.sub(r"\bvs\.?\b", "v.", t)
    t = re.sub(r"\s+v\.\s+", " v. ", t)
    t = re.sub(r"[\u2013\u2014\-:;,\.\(\)\[\]“”\"']", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t

def unique_cases(entries):
    seen = set()
    out = []
    for e in entries:
        key = normalize_case_key(e["title"])
        if key in seen: 
            continue
        seen.add(key)
        out.append(e)
    return out

# ---------- generic extractor (with strict filtering) ----------
def extract_entries_generic(soup, url, source_name):
    entries = []
    blocks = []
    for sel in ["article", "li", "div.card", "div.item", "div.teaser", "div.news-item", "div.post", "div.result", "section"]:
        blocks.extend(soup.select(sel))
    for b in blocks:
        title_el = b.select_one("h1, h2, h3, a[title], a")
        if not title_el:
            continue
        title = clean(title_el.get_text())
        if not title or not is_probable_case(title):
            continue
        # prefer nearby summary/date
        date_el = b.select_one("time, .date, .news-date, span.date")
        sum_el  = b.select_one("p, .summary, .teaser, .excerpt, div, span")
        date = clean(date_el.get_text() if date_el else "")
        summary = clean(sum_el.get_text() if sum_el else "")
        entries.append({
            "title": title,
            "date": date,
            "summary": summary,
            "source": source_name,
            "url": url,
            "outcome": infer_outcome_short(f"{title} {summary}")
        })
    return entries

# ---------- per-source scrapers (fast to maintain) ----------
def scrape_mckool():
    url = "https://www.mckoolsmith.com/newsroom-ailitigation"
    soup = BeautifulSoup(get_html(url), "html.parser")
    return extract_entries_generic(soup, url, "McKool Smith")

def scrape_bakerhostetler():
    url = "https://www.bakerlaw.com/services/artificial-intelligence-ai/case-tracker-artificial-intelligence-copyrights-and-class-actions/"
    soup = BeautifulSoup(get_html(url), "html.parser")
    return extract_entries_generic(soup, url, "BakerHostetler")

def scrape_wired():
    url = "https://www.wired.com/story/ai-copyright-case-tracker/"
    soup = BeautifulSoup(get_html(url), "html.parser")
    return extract_entries_generic(soup, url, "WIRED")

def scrape_mishcon():
    url = "https://www.mishcon.com/generative-ai-intellectual-property-cases-and-policy-tracker"
    soup = BeautifulSoup(get_html(url), "html.parser")
    return extract_entries_generic(soup, url, "Mishcon de Reya LLP")

def scrape_cms():
    url = "https://cms.law/en/int/publication/artificial-intelligence-and-copyright-case-tracker"
    soup = BeautifulSoup(get_html(url), "html.parser")
    return extract_entries_generic(soup, url, "CMS Law")

SOURCES = [scrape_mckool, scrape_bakerhostetler, scrape_wired, scrape_mishcon, scrape_cms]  # priority order

# ---------- issue body + site writer ----------
def make_issue_body(entry, key_hex):
    headline = entry["title"]
    date_line = f"**Date/Update**: {entry.get('date') or 'N/A'}"
    outcome_line = f"**Outcome (short)**: {entry.get('outcome') or 'Update'}"
    src = entry.get("source", "Unknown")
    url = entry.get("url", "")
    summary = entry.get("summary") or "Summary coming soon."

    # Match your template: 1-line headline, then tight para, then Key takeaway.
    body = f"""{headline}

{date_line}
{outcome_line}
**Source:** {src} ({url})

**Summary:** {summary}

**Key takeaway:** Even where training might be fair use in some contexts, liability can still arise from acquisition/collection practices or market harm—track facts by case.
<!-- KEY: {key_hex} -->
"""
    return body

def ensure_docs_shell():
    os.makedirs(DOCS_DIR, exist_ok=True)
    open(os.path.join(DOCS_DIR, ".nojekyll"), "a").close()  # disable Jekyll
    index_path = os.path.join(DOCS_DIR, "index.html")
    if os.path.exists(index_path):
        return
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(_INDEX_HTML)

def run():
    # scrape + filter
    all_entries = []
    for fn in SOURCES:
        try:
            all_entries.extend(fn())
        except Exception as e:
            print(f"[WARN] {fn.__name__} failed: {e}")

    # drop anything that slipped past filters
    all_entries = [e for e in all_entries if is_probable_case(e["title"])]

    # de-dup across sources (first source in SOURCES wins)
    deduped = unique_cases(all_entries)

    # labels + issues
    source_labels = {SOURCE_LABEL_PREFIX + (e.get("source") or "Unknown") for e in deduped}
    ensure_labels(list(source_labels))
    existing = list_existing_issue_keys()

    created = 0
    for e in deduped:
        key_hex = hashlib.md5((normalize_case_key(e["title"]) + "|" + (e.get("source") or "")).encode("utf-8")).hexdigest()
        if key_hex in existing:
            continue
        body = make_issue_body(e, key_hex)
        labels = COMMON_LABELS + [SOURCE_LABEL_PREFIX + e.get("source", "Unknown")]
        try:
            create_issue(e["title"], body, labels)
            created += 1
        except Exception as ex:
            print(f"[ERROR] issue create failed for '{e['title']}': {ex}")

    print(f"Issues created: {created}")

    # site output
    ensure_docs_shell()
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(deduped, f, ensure_ascii=False, indent=2)
    print(f"Wrote {JSON_PATH} with {len(deduped)} cases.")

# ------------- CLEAN UI (cards) -------------
_INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>AI Court Cases Tracker</title>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<style>
  :root { --fg: #0f172a; --muted:#475569; --bg:#ffffff; --card:#f8fafc; --line:#e2e8f0; }
  *{box-sizing:border-box}
  body{font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; color:var(--fg); background:var(--bg); margin:24px;}
  h1{margin:0 0 6px 0; font-size:28px; font-weight:750}
  .sub{color:var(--muted); margin-bottom:16px}
  .toolbar{display:flex; gap:12px; margin:10px 0 18px; flex-wrap:wrap}
  input,select{padding:10px 12px; border:1px solid var(--line); border-radius:10px; font-size:14px}
  .grid{display:grid; grid-template-columns:repeat(auto-fill, minmax(320px,1fr)); gap:14px}
  .card{background:var(--card); border:1px solid var(--line); border-radius:14px; padding:14px; display:flex; flex-direction:column; gap:8px}
  .title{font-weight:700}
  .meta{font-size:12px; color:var(--muted)}
  .outcome{font-weight:600}
  .takeaway{border-top:1px dashed var(--line); padding-top:8px; font-size:14px}
  a{color:#0ea5e9; text-decoration:none}
  a:hover{text-decoration:underline}
</style>
</head>
<body>
  <h1>AI Court Cases Tracker</h1>
  <div class="sub">De-duplicated case summaries across multiple public trackers. Updates via GitHub Actions.</div>
  <div class="toolbar">
    <input id="q" type="search" placeholder="Filter by case, court, outcome, source…"/>
    <select id="sort">
      <option value="title">Sort: Title</option>
      <option value="date">Sort: Date</option>
      <option value="source">Sort: Source</option>
    </select>
  </div>
  <div id="list" class="grid"></div>

<script>
async function load() {
  const res = await fetch('cases.json', {cache:'no-store'});
  const data = await res.json();

  const list = document.getElementById('list');
  const q = document.getElementById('q');
  const sortSel = document.getElementById('sort');

  function render(filter='', sortBy='title') {
    const f = filter.toLowerCase();
    let items = data.filter(c => {
      const hay = (c.title + ' ' + (c.outcome||'') + ' ' + (c.source||'') + ' ' + (c.summary||'') + ' ' + (c.date||'')).toLowerCase();
      return !f || hay.includes(f);
    });

    items.sort((a,b)=>{
      const A=(a[sortBy]||'').toString().toLowerCase();
      const B=(b[sortBy]||'').toString().toLowerCase();
      return A.localeCompare(B);
    });

    list.innerHTML = '';
    items.forEach(c=>{
      const el = document.createElement('div');
      el.className = 'card';
      const headline = c.title;  // already in "Foo v. Bar …" style
      const summary = c.summary || 'Summary unavailable from source.';
      const outcome = c.outcome || 'Update';
      const date = c.date || 'N/A';
      const src = c.source || 'Source';
      const url = c.url || '#';
      el.innerHTML = `
        <div class="title">${headline}</div>
        <div class="meta">${src} • ${date} • <span class="outcome">${outcome}</span> • <a href="${url}" target="_blank" rel="noopener">Source</a></div>
        <div class="summary">${summary}</div>
        <div class="takeaway"><strong>Key takeaway:</strong> Even where training may be argued as transformative, acquisition and market harm remain decisive issues. Track facts by case.</div>
      `;
      list.appendChild(el);
    });
  }

  q.addEventListener('input', (e)=>render(e.target.value, sortSel.value));
  sortSel.addEventListener('change', ()=>render(q.value, sortSel.value));
  render();
}
load();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    run()

import os
import re
import json
import hashlib
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

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

# ---------- CASE PATTERNS ----------
CASE_PATTERNS = [
    r"\b[A-Z][\w'.&-]{1,40}\s+v\.\s+[A-Z][\w'.&-]{1,60}\b",  # Foo v. Bar
    r"\b[A-Z][\w'.&-]{1,40}\s+vs\.?\s+[A-Z][\w'.&-]{1,60}\b",# Foo vs Bar
    r"\bIn\s+re\s+[A-Z][\w'.&-]{2,}\b",                     # In re Something
    r"\bU\.S\.\s+v\.\s+[A-Z][\w'.&-]{1,60}\b",              # U.S. v. X
]
CASE_REGEX = re.compile("|".join(CASE_PATTERNS))

JUNK_TITLE_PHRASES = [
    "litigation tracker","case updates","current edition","disclaimer",
    "artificial intelligence (ai)","training & development","newsletter","event:"
]

# ---------- GitHub helpers ----------
def gh_headers():
    if not TOKEN:
        raise RuntimeError("Missing token. Map secrets.PAT_TOKEN to PERSONAL_ACCESS_TOKEN.")
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
            r = requests.get(f"{API_BASE}/issues", headers=gh_headers(),
                             params={"state": state, "per_page": 100, "page": page}, timeout=TIMEOUT)
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

def is_probable_case(text):
    return bool(CASE_REGEX.search(text))

def infer_outcome_short(text):
    m = re.search(
        r"(fair use|summary judgment|partial summary judgment|dismiss(ed)?|prelim(inary)? injunction|injunction|"
        r"settle(d)?|class action|certification|md(l)?|transfer|stay(ed)?|remand|contempt|damages|verdict|trial)",
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

def absolutize(base, href):
    if not href: return None
    return urljoin(base, href)

# ---------- parse article page to extract case lines ----------
def extract_cases_from_article(url):
    try:
        html = get_html(url)
    except Exception:
        return []
    soup = BeautifulSoup(html, "html.parser")
    texts = []
    # grab paragraphs and list items
    for sel in ["p", "li", "h2", "h3"]:
        for el in soup.select(sel):
            t = clean(el.get_text())
            if len(t) > 0:
                texts.append(t)
    cases = []
    for t in texts:
        if is_probable_case(t):
            # build a short summary (take sentence around the match)
            outcome = infer_outcome_short(t)
            # try to limit to first ~220 chars for UI
            summary = t if len(t) <= 220 else t[:217] + "..."
            # case title is the first matched case-like span
            m = CASE_REGEX.search(t)
            title = m.group(0) if m else t.split(". ")[0][:120]
            cases.append({
                "title": title,
                "date": "",               # many pages don’t expose date cleanly; can refine later
                "summary": summary,
                "source": "",             # filled by caller
                "url": url,
                "outcome": outcome
            })
    return cases

# ---------- list page extractor: follow article links, mine cases inside ----------
def harvest_from_listing(list_url, source_name, link_selectors, block_selectors=None):
    html = get_html(list_url)
    soup = BeautifulSoup(html, "html.parser")

    # Collect candidate article links
    links = set()
    if block_selectors:
        blocks = []
        for sel in block_selectors:
            blocks.extend(soup.select(sel))
        for b in blocks:
            for sel in link_selectors:
                a = b.select_one(sel)
                if a and a.get("href"):
                    links.add(absolutize(list_url, a["href"]))
    else:
        for sel in link_selectors:
            for a in soup.select(sel):
                if a and a.get("href"):
                    links.add(absolutize(list_url, a["href"]))

    entries = []
    for href in links:
        if not href: 
            continue
        # only crawl pages within same host (avoid unrelated promos)
        if urlparse(href).netloc and urlparse(href).netloc not in urlparse(list_url).netloc:
            continue
        article_cases = extract_cases_from_article(href)
        for c in article_cases:
            c["source"] = source_name
        entries.extend(article_cases)

    # log count for visibility
    print(f"[{source_name}] articles: {len(links)} • cases extracted: {len(entries)}")
    return entries

# ---------- per-source scrapers ----------
def scrape_mckool():
    # Main tracker page links to weekly updates/articles; follow those and extract "v." lines inside
    list_url = "https://www.mckoolsmith.com/newsroom-ailitigation"
    return harvest_from_listing(
        list_url, "McKool Smith",
        link_selectors=["a", "h2 a", "h3 a"],
        block_selectors=["article", "li", "div.item", "div.card", "div.teaser", "section"]
    )

def scrape_bakerhostetler():
    list_url = "https://www.bakerlaw.com/services/artificial-intelligence-ai/case-tracker-artificial-intelligence-copyrights-and-class-actions/"
    return harvest_from_listing(
        list_url, "BakerHostetler",
        link_selectors=["a", "h2 a", "h3 a"],
        block_selectors=["article", "li", "div", "section"]
    )

def scrape_wired():
    list_url = "https://www.wired.com/story/ai-copyright-case-tracker/"
    return harvest_from_listing(
        list_url, "WIRED",
        link_selectors=["a", "h2 a", "h3 a"],
        block_selectors=["article", "li", "div", "section"]
    )

def scrape_mishcon():
    list_url = "https://www.mishcon.com/generative-ai-intellectual-property-cases-and-policy-tracker"
    return harvest_from_listing(
        list_url, "Mishcon de Reya LLP",
        link_selectors=["a", "h2 a", "h3 a"],
        block_selectors=["article", "li", "div", "section"]
    )

def scrape_cms():
    list_url = "https://cms.law/en/int/publication/artificial-intelligence-and-copyright-case-tracker"
    return harvest_from_listing(
        list_url, "CMS Law",
        link_selectors=["a", "h2 a", "h3 a"],
        block_selectors=["article", "li", "div", "section"]
    )

SOURCES = [scrape_mckool, scrape_bakerhostetler, scrape_wired, scrape_mishcon, scrape_cms]  # priority order

# ---------- issues + site ----------
def make_issue_body(entry, key_hex):
    headline = entry["title"]
    outcome_line = f"**Outcome (short)**: {entry.get('outcome') or 'Update'}"
    src = entry.get("source", "Unknown")
    url = entry.get("url", "")
    summary = entry.get("summary") or "Summary coming soon."

    # Your requested concise style
    body = f"""{headline}
{outcome_line}
**Summary:** {summary}
**Source:** {src} ({url})
**Key takeaway:** Focus pleadings on concrete market harm & acquisition conduct; training fair use depends on record.

<!-- KEY: {key_hex} -->
"""
    return body

def ensure_site_shell_overwrite():
    os.makedirs(DOCS_DIR, exist_ok=True)
    # always (re)write index each run so old placeholder never persists
    with open(os.path.join(DOCS_DIR, ".nojekyll"), "w", encoding="utf-8") as f:
        f.write("")
    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(_INDEX_HTML)

def run():
    # 1) scrape: follow links, then mine case lines inside pages
    all_entries = []
    for fn in SOURCES:
        try:
            all_entries.extend(fn())
        except Exception as e:
            print(f"[WARN] {fn.__name__} failed: {e}")

    # 2) keep only real cases
    all_entries = [e for e in all_entries if is_probable_case(e["title"])]

    # 3) de-dup across sources (first source wins)
    deduped = unique_cases(all_entries)
    print(f"[TOTAL] deduped cases: {len(deduped)}")

    # 4) GitHub Issues
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

    # 5) Website artifacts (always overwrite index; write JSON)
    ensure_site_shell_overwrite()
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(deduped, f, ensure_ascii=False, indent=2)
    print(f"Wrote {JSON_PATH} with {len(deduped)} cases.")

# ------- UI (clean cards) -------
_INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>AI Court Cases Tracker</title>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<style>
  :root { --fg:#0f172a; --muted:#475569; --bg:#ffffff; --card:#f8fafc; --line:#e2e8f0; }
  *{box-sizing:border-box}
  body{font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; color:var(--fg); background:var(--bg); margin:24px;}
  h1{margin:0 0 6px 0; font-size:28px; font-weight:750}
  .sub{color:var(--muted); margin-bottom:16px}
  .toolbar{display:flex; gap:12px; margin:10px 0 18px; flex-wrap:wrap}
  input,select{padding:10px 12px; border:1px solid var(--line); border-radius:10px; font-size:14px}
  .grid{display:grid; grid-template-columns:repeat(auto-fill, minmax(340px,1fr)); gap:14px}
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
    <input id="q" type="search" placeholder="Filter by case, outcome, source…"/>
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
      el.innerHTML = `
        <div class="title">${c.title}</div>
        <div class="meta">${c.source || ''} • ${c.date || 'N/A'} • <span class="outcome">${c.outcome || 'Update'}</span> • <a href="${c.url || '#'}" target="_blank" rel="noopener">Source</a></div>
        <div class="summary">${c.summary || 'Summary unavailable from source.'}</div>
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

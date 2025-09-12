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

def infer_status(text):
    t = text.lower()
    if any(k in t for k in ["recently filed", "filed on", "filed ", "new case", "complaint"]):
        return "Recently filed"
    if any(k in t for k in ["summary judgment", "granted judgment", "judgment entered", "verdict", "liability"]):
        return "Judgment"
    if any(k in t for k in ["dismissed", "dismissal", "motion to dismiss granted"]):
        return "Dismissed"
    if any(k in t for k in ["preliminary injunction", "permanent injunction", "injunction"]):
        return "Injunction"
    if any(k in t for k in ["settled", "settlement"]):
        return "Settled"
    if any(k in t for k in ["class certification", "certified class", "class certified"]):
        return "Class certified"
    if any(k in t for k in ["mdl", "transferred", "transfer order", "centralized"]):
        return "MDL/Transfer"
    if any(k in t for k in ["stayed", "remand"]):
        return "Stayed/Remand"
    return "Open/Active"

def infer_outcome_short(text):
    m = re.search(
        r"(fair use|summary judgment|partial summary judgment|dismiss(ed)?|prelim(inary)? injunction|injunction|"
        r"settle(d)?|class certification|class action|md(l)?|transfer|stay(ed)?|remand|verdict|trial|damages)",
        text, re.I
    )
    return clean(m.group(0)).capitalize() if m else "Update"

def generate_takeaway(text):
    t = text.lower()
    if "fair use" in t and "pirat" in t:
        return "Even where training is fair use, downloading pirated content can still trigger liability."
    if "fair use" in t and "market" in t:
        return "Fair use can turn on evidence of market harm to the original works."
    if "injunction" in t:
        return "Preliminary injunctions may issue where likelihood-of-success and irreparable harm are shown."
    if "dismiss" in t:
        return "Pleadings must tie copying to cognizable harm; conclusory allegations risk dismissal."
    if "class" in t and "certif" in t:
        return "Class certification depends on commonality and predominance; records-heavy issues can defeat it."
    if "settle" in t:
        return "Parties continue to resolve AI/IP disputes without merits rulings."
    return ""

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

def url_abs(base, href):
    if not href: return None
    return urljoin(base, href)

# ---------- extract case lines from article ----------
def extract_cases_from_article(url):
    try:
        html = get_html(url)
    except Exception:
        return []
    soup = BeautifulSoup(html, "html.parser")

    # collect text from p/li/headings; split into sentences
    texts = []
    for sel in ["p", "li", "h2", "h3"]:
        for el in soup.select(sel):
            t = clean(el.get_text())
            if t:
                # break into sentence-ish units
                parts = re.split(r"(?<=[\.\?!])\s+", t)
                texts.extend([p for p in parts if p])

    cases = []
    for i, sent in enumerate(texts):
        if not is_probable_case(sent):
            continue
        # build a 1–3 sentence summary: sentence with match + following sentence if helpful
        summary_sents = [sent]
        if i + 1 < len(texts) and len(texts[i+1]) > 40:
            summary_sents.append(texts[i+1])
        if i + 2 < len(texts) and len(" ".join(summary_sents)) < 320:
            summary_sents.append(texts[i+2])
        summary = " ".join(summary_sents)
        # title = matched case phrase
        m = CASE_REGEX.search(sent)
        title = m.group(0) if m else sent[:120]
        outcome = infer_outcome_short(summary)
        status = infer_status(summary)
        takeaway = generate_takeaway(summary)

        cases.append({
            "title": title,
            "headline": f"{title}",  # UI can extend with court/finding when available
            "date": "",               # can be refined per-source if dates are exposed
            "summary": summary[:700],
            "source": "",             # filled by caller
            "url": url,
            "outcome": outcome,
            "status": status,
            "takeaway": takeaway
        })
    return cases

# ---------- list page extractor: follow article links ----------
def harvest_from_listing(list_url, source_name, link_selectors, block_selectors=None):
    html = get_html(list_url)
    soup = BeautifulSoup(html, "html.parser")

    links = set()
    if block_selectors:
        blocks = []
        for sel in block_selectors:
            blocks.extend(soup.select(sel))
        for b in blocks:
            for sel in link_selectors:
                a = b.select_one(sel)
                if a and a.get("href"):
                    links.add(url_abs(list_url, a["href"]))
    else:
        for sel in link_selectors:
            for a in soup.select(sel):
                if a and a.get("href"):
                    links.add(url_abs(list_url, a["href"]))

    entries = []
    for href in links:
        if not href: 
            continue
        if urlparse(href).netloc and urlparse(href).netloc not in urlparse(list_url).netloc:
            continue
        article_cases = extract_cases_from_article(href)
        for c in article_cases:
            c["source"] = source_name
        entries.extend(article_cases)

    print(f"[{source_name}] articles: {len(links)} • cases extracted: {len(entries)}")
    return entries

# ---------- per-source scrapers ----------
def scrape_mckool():
    list_url = "https://www.mckoolsmith.com/newsroom-ailitigation"
    return harvest_from_listing(
        list_url, "McKool Smith",
        link_selectors=["h1 a", "h2 a", "h3 a", "a"],
        block_selectors=["article", "li", "div.item", "div.card", "div.teaser", "section"]
    )

def scrape_bakerhostetler():
    list_url = "https://www.bakerlaw.com/services/artificial-intelligence-ai/case-tracker-artificial-intelligence-copyrights-and-class-actions/"
    return harvest_from_listing(
        list_url, "BakerHostetler",
        link_selectors=["h1 a", "h2 a", "h3 a", "a"],
        block_selectors=["article", "li", "div", "section"]
    )

def scrape_wired():
    list_url = "https://www.wired.com/story/ai-copyright-case-tracker/"
    return harvest_from_listing(
        list_url, "WIRED",
        link_selectors=["h1 a", "h2 a", "h3 a", "a"],
        block_selectors=["article", "li", "div", "section"]
    )

def scrape_mishcon():
    list_url = "https://www.mishcon.com/generative-ai-intellectual-property-cases-and-policy-tracker"
    return harvest_from_listing(
        list_url, "Mishcon de Reya LLP",
        link_selectors=["h1 a", "h2 a", "h3 a", "a"],
        block_selectors=["article", "li", "div", "section"]
    )

def scrape_cms():
    list_url = "https://cms.law/en/int/publication/artificial-intelligence-and-copyright-case-tracker"
    return harvest_from_listing(
        list_url, "CMS Law",
        link_selectors=["h1 a", "h2 a", "h3 a", "a"],
        block_selectors=["article", "li", "div", "section"]
    )

SOURCES = [scrape_mckool, scrape_bakerhostetler, scrape_wired, scrape_mishcon, scrape_cms]  # priority order

# ---------- issues + site ----------
def make_issue_body(entry, key_hex):
    # Format like your template: Headline line, then clean paragraph, then Key takeaway
    headline = entry["headline"]
    summary = entry["summary"]
    takeaway = entry.get("takeaway", "")
    src = entry.get("source", "Unknown")
    url = entry.get("url", "")
    outcome = entry.get("outcome") or "Update"
    status = entry.get("status") or "Open/Active"

    # Combine into one tight paragraph (no generic filler)
    body = f"""{headline} – {outcome}.
{summary}
Key takeaway: {takeaway or '—'}

Status: {status}
Source: {src} ({url})

<!-- KEY: {key_hex} -->
"""
    return body

def ensure_site_shell_overwrite():
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(os.path.join(DOCS_DIR, ".nojekyll"), "w", encoding="utf-8") as f:
        f.write("")
    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(_INDEX_HTML)

def run():
    # 1) scrape
    all_entries = []
    for fn in SOURCES:
        try:
            all_entries.extend(fn())
        except Exception as e:
            print(f"[WARN] {fn.__name__} failed: {e}")

    # 2) real cases only
    all_entries = [e for e in all_entries if is_probable_case(e["title"])]

    # 3) de-dup across sources
    deduped = unique_cases(all_entries)
    print(f"[TOTAL] deduped cases: {len(deduped)}")

    # 4) labels + issues
    source_labels = {SOURCE_LABEL_PREFIX + (e.get("source") or "Unknown") for e in deduped}
    ensure_labels(list(source_labels))
    existing = list_existing_issue_keys()
    created = 0
    for e in deduped:
        key_hex = hashlib.md5((normalize_case_key(e["title"]) + "|" + (e.get("source") or "")).encode("utf-8")).hexdigest()
        if key_hex in existing:
            continue
        body = make_issue_body(e, key_hex)
        labels = COMMON_LABELS + [SOURCE_LABEL_PREFIX + e.get("source", "Unknown"), e.get("status","Open/Active")]
        try:
            create_issue(e["headline"], body, labels)
            created += 1
        except Exception as ex:
            print(f"[ERROR] issue create failed for '{e['headline']}': {ex}")
    print(f"Issues created: {created}")

    # 5) Website artifacts (overwrite index; JSON with status/takeaway)
    ensure_site_shell_overwrite()
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(deduped, f, ensure_ascii=False, indent=2)
    print(f"Wrote {JSON_PATH} with {len(deduped)} cases.")

# ------- UI (uniform fonts, status badge, clean cards) -------
_INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>AI Court Cases Tracker</title>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<style>
  :root { --fg:#0f172a; --muted:#475569; --bg:#ffffff; --card:#f8fafc; --line:#e2e8f0; --pill:#0ea5e9; }
  *{box-sizing:border-box}
  html,body{margin:0;padding:0}
  body{font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; color:var(--fg); background:var(--bg); padding:24px;}
  h1{margin:0 0 6px 0; font-size:24px; font-weight:700}
  .sub{color:var(--muted); margin-bottom:16px; font-size:14px}
  .toolbar{display:flex; gap:12px; margin:10px 0 18px; flex-wrap:wrap}
  input,select{padding:10px 12px; border:1px solid var(--line); border-radius:10px; font-size:14px}
  .grid{display:grid; grid-template-columns:repeat(auto-fill, minmax(380px,1fr)); gap:14px}
  .card{background:var(--card); border:1px solid var(--line); border-radius:14px; padding:14px; display:flex; flex-direction:column; gap:10px}
  .title{font-weight:700; font-size:16px; line-height:1.35}
  .meta{font-size:12px; color:var(--muted); display:flex; gap:8px; align-items:center; flex-wrap:wrap}
  .pill{background:var(--pill); color:#fff; border-radius:999px; padding:3px 8px; font-size:11px; font-weight:600}
  .outcome{font-weight:600}
  .summary{font-size:14px; line-height:1.45}
  .takeaway{border-top:1px dashed var(--line); padding-top:8px; font-size:13px}
  a{color:#0ea5e9; text-decoration:none}
  a:hover{text-decoration:underline}
</style>
</head>
<body>
  <h1>AI Court Cases Tracker</h1>
  <div class="sub">De-duplicated case summaries across multiple public trackers. Updates via GitHub Actions.</div>
  <div class="toolbar">
    <input id="q" type="search" placeholder="Filter by case, outcome, source…" aria-label="Filter"/>
    <select id="sort" aria-label="Sort">
      <option value="title">Sort: Title</option>
      <option value="status">Sort: Status</option>
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
      const hay = (c.title + ' ' + (c.outcome||'') + ' ' + (c.source||'') + ' ' + (c.summary||'') + ' ' + (c.status||'')).toLowerCase();
      return !f || hay.includes(f);
    });

    items.sort((a,b)=>{
      const A=(a[sortBy]||'').toString().toLowerCase();
      const B=(b[sortBy]||'').toString().toLowerCase();
      return A.localeCompare(B);
    });

    list.innerHTML = '';
    items.forEach(c=>{
      const url = c.url || '#';
      const takeawayLine = c.takeaway ? `<div class="takeaway"><strong>Key takeaway:</strong> ${c.takeaway}</div>` : '';
      const outcome = c.outcome || 'Update';
      const status = c.status || 'Open/Active';
      const headline = c.headline || c.title;
      list.innerHTML += `
        <div class="card">
          <div class="title">${headline} – ${outcome}.</div>
          <div class="meta">
            <span class="pill">${status}</span>
            <span>${c.source || ''}</span>
            <a href="${url}" target="_blank" rel="noopener">Source</a>
          </div>
          <div class="summary">${c.summary || 'No summary provided by source.'}</div>
          ${takeawayLine}
        </div>`;
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

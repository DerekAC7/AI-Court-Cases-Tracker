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
DOCS_DIR = "docs"           # GitHub Pages will serve from /docs on main
JSON_PATH = f"{DOCS_DIR}/cases.json"
# =========================

API_BASE = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}"

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
    """
    De-dup GitHub issues using a stored hash key in the body: <!-- KEY: ... -->
    """
    keys = set()
    for state in ("open", "closed"):
        page = 1
        while True:
            r = requests.get(f"{API_BASE}/issues",
                             headers=gh_headers(),
                             params={"state": state, "per_page": 100, "page": page},
                             timeout=TIMEOUT)
            r.raise_for_status()
            items = r.json()
            if not items:
                break
            for it in items:
                if "pull_request" in it:
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

# ---------- HTTP / parsing ----------
def get_html(url):
    return requests.get(url, timeout=TIMEOUT).text

def clean(text):
    return re.sub(r"\s+", " ", (text or "").strip())

def extract_entries_generic(soup):
    """
    Fallback extractor if a site changes layout.
    Returns list of dicts with keys: title, date, summary
    """
    entries = []
    blocks = []
    for sel in ["article", "li", "div.card", "div.item", "div.teaser", "div.news-item", "div.post", "div.result"]:
        found = soup.select(sel)
        if found:
            blocks.extend(found)
    seen = set()
    for b in blocks:
        title_el = b.select_one("h1, h2, h3, a[title], a")
        date_el  = b.select_one("time, .date, .news-date, span.date")
        sum_el   = b.select_one("p, .summary, .teaser, .excerpt")
        title = clean(title_el.get_text() if title_el else "")
        if not title or title in seen:
            continue
        seen.add(title)
        # keep likely case or strongly AI/IP relevant
        looks_like_case = (" v. " in title) or (" v." in title) or (" vs " in title.lower())
        if not looks_like_case:
            if not any(k in title.lower() for k in ["ai", "copyright", "midjourney", "openai", "anthropic", "meta", "suno", "udio", "stability"]):
                continue
        entries.append({
            "title": title,
            "date": clean(date_el.get_text() if date_el else ""),
            "summary": clean(sum_el.get_text() if sum_el else "")
        })
    return entries

def infer_outcome_short(text):
    m = re.search(r"(fair use|summary judgment|dismiss(ed)?|prelim(inary)? injunction|injunction|settle(d)?|class action|stay(ed)?|remand)", text, re.I)
    return clean(m.group(0)).capitalize() if m else "Update"

# ---------- Source scrapers ----------
def scrape_mckool():
    url = "https://www.mckoolsmith.com/newsroom-ailitigation"
    soup = BeautifulSoup(get_html(url), "html.parser")
    items = extract_entries_generic(soup)
    for it in items:
        it["source"] = "McKool Smith"
        it["url"] = url
        it["outcome"] = infer_outcome_short(f"{it['title']} {it['summary']}")
    return items

def scrape_bakerhostetler():
    url = "https://www.bakerlaw.com/services/artificial-intelligence-ai/case-tracker-artificial-intelligence-copyrights-and-class-actions/"
    soup = BeautifulSoup(get_html(url), "html.parser")
    items = extract_entries_generic(soup)
    for it in items:
        it["source"] = "BakerHostetler"
        it["url"] = url
        it["outcome"] = infer_outcome_short(f"{it['title']} {it['summary']}")
    return items

def scrape_wired():
    url = "https://www.wired.com/story/ai-copyright-case-tracker/"
    soup = BeautifulSoup(get_html(url), "html.parser")
    items = extract_entries_generic(soup)
    for it in items:
        it["source"] = "WIRED"
        it["url"] = url
        it["outcome"] = infer_outcome_short(f"{it['title']} {it['summary']}")
    return items

def scrape_mishcon():
    url = "https://www.mishcon.com/generative-ai-intellectual-property-cases-and-policy-tracker"
    soup = BeautifulSoup(get_html(url), "html.parser")
    items = extract_entries_generic(soup)
    for it in items:
        it["source"] = "Mishcon de Reya LLP"
        it["url"] = url
        it["outcome"] = infer_outcome_short(f"{it['title']} {it['summary']}")
    return items

def scrape_cms():
    url = "https://cms.law/en/int/publication/artificial-intelligence-and-copyright-case-tracker"
    soup = BeautifulSoup(get_html(url), "html.parser")
    items = extract_entries_generic(soup)
    for it in items:
        it["source"] = "CMS Law"
        it["url"] = url
        it["outcome"] = infer_outcome_short(f"{it['title']} {it['summary']}")
    return items

SOURCES = [
    # Priority order for de-dup (first source wins)
    scrape_mckool,
    scrape_bakerhostetler,
    scrape_wired,
    scrape_mishcon,
    scrape_cms,
]

# ---------- De-duplication ----------
def normalize_case_key(title):
    t = title.lower()
    # normalize common variants of "v." / "vs"
    t = re.sub(r"\bvs\.?\b", "v.", t)
    t = re.sub(r"\s+v\.\s+", " v. ", t)
    # collapse whitespace & punctuation noise
    t = re.sub(r"[\u2013\u2014\-:;,\.\(\)\[\]]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    # only keep up to the first sentence-ish for robustness
    t = t.split(" – ")[0].split(" — ")[0].split(". ")[0]
    return t

def unique_cases(entries):
    seen = set()
    unique = []
    for e in entries:
        key = normalize_case_key(e["title"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(e)
    return unique

# ---------- Issue body / site output ----------
def make_issue_body(entry, key_hex):
    headline = entry["title"]
    date_line = f"**Date/Update**: {entry.get('date') or 'N/A'}"
    outcome_line = f"**Outcome (short)**: {entry.get('outcome') or 'Update'}"
    src = entry.get("source", "Unknown")
    url = entry.get("url", "")
    summary = entry.get("summary") or "Summary coming soon."

    body = f"""{headline}

{date_line}
{outcome_line}
**Source:** {src} ({url})

**Summary:** {summary}

**Key takeaway:** _Add takeaway once confirmed._

<!-- KEY: {key_hex} -->
"""
    return body

def ensure_docs_index_exists():
    """
    Create a very simple site if /docs/index.html doesn't exist.
    """
    os.makedirs(DOCS_DIR, exist_ok=True)
    index_path = f"{DOCS_DIR}/index.html"
    if os.path.exists(index_path):
        return
    html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>AI Court Cases Tracker</title>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 24px; }
    h1 { margin-bottom: 8px; }
    .meta { color:#555; margin-bottom: 20px; }
    .case { border: 1px solid #ddd; border-radius: 12px; padding: 14px; margin: 10px 0; }
    .src { font-size: 12px; color:#666; }
    .search { margin: 10px 0 20px; }
    input[type="search"] { width: 100%; padding: 10px; font-size: 16px; }
    .outcome { font-weight: 600; }
  </style>
</head>
<body>
  <h1>AI Court Cases Tracker</h1>
  <div class="meta">Auto-generated from multiple public trackers. De-duplicated across sources.</div>
  <div class="search">
    <input id="q" type="search" placeholder="Filter by case name, outcome, source..."/>
  </div>
  <div id="list"></div>

  <script>
    async function load() {
      const res = await fetch('cases.json', {cache:'no-store'});
      const data = await res.json();
      const list = document.getElementById('list');
      const q = document.getElementById('q');

      function render(filter='') {
        const f = filter.toLowerCase();
        list.innerHTML = '';
        data.forEach(c => {
          const hay = (c.title + ' ' + (c.outcome||'') + ' ' + (c.source||'') + ' ' + (c.summary||'')).toLowerCase();
          if (f && !hay.includes(f)) return;
          const el = document.createElement('div');
          el.className = 'case';
          el.innerHTML = `
            <div class="src">${c.source} • ${c.date || 'N/A'}</div>
            <div class="title"><strong>${c.title}</strong></div>
            <div class="outcome">${c.outcome || 'Update'}</div>
            <div class="summary">${c.summary || ''}</div>
            <div class="src"><a href="${c.url}" target="_blank" rel="noopener">Source</a></div>
          `;
          list.appendChild(el);
        });
      }
      q.addEventListener('input', (e)=>render(e.target.value));
      render();
    }
    load();
  </script>
</body>
</html>
"""
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html)

def run():
    # 1) scrape all sources (tolerate per-source failures)
    all_entries = []
    for fn in SOURCES:
        try:
            all_entries.extend(fn())
        except Exception as e:
            print(f"[WARN] {fn.__name__} failed: {e}")

    # 2) de-duplicate across sources
    deduped = unique_cases(all_entries)

    # 3) labels & issues
    source_labels = {SOURCE_LABEL_PREFIX + (e.get("source") or "Unknown") for e in deduped}
    ensure_labels(list(source_labels))
    existing_issue_keys = list_existing_issue_keys()

    created = 0
    for e in deduped:
        # hash key based on normalized title + source kept for audit
        key_hex = hashlib.md5((normalize_case_key(e["title"]) + "|" + (e.get("source") or "")).encode("utf-8")).hexdigest()
        if key_hex in existing_issue_keys:
            continue
        labels = COMMON_LABELS + [SOURCE_LABEL_PREFIX + e.get("source", "Unknown")]
        body = make_issue_body(e, key_hex)
        try:
            create_issue(e["title"], body, labels)
            created += 1
        except Exception as ex:
            print(f"[ERROR] create issue for '{e['title']}' failed: {ex}")

    print(f"Issues created: {created}")

    # 4) write website artifacts
    ensure_docs_index_exists()
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(deduped, f, ensure_ascii=False, indent=2)
    print(f"Wrote site data: {JSON_PATH} ({len(deduped)} cases)")

if __name__ == "__main__":
    run()

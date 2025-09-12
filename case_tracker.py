#!/usr/bin/env python3
import os, re, json, time, html
import requests
from bs4 import BeautifulSoup

DOCS_DIR = "docs"
JSON_PATH = os.path.join(DOCS_DIR, "cases.json")

SOURCES = [
    {
        "name": "McKool Smith",
        "url": "https://www.mckoolsmith.com/newsroom-ailitigation",
        "type": "html",
    },
    {
        "name": "BakerHostetler",
        "url": "https://www.bakerlaw.com/services/artificial-intelligence-ai/case-tracker-artificial-intelligence-copyrights-and-class-actions/",
        "type": "html",
    },
    {
        "name": "WIRED",
        "url": "https://www.wired.com/story/ai-copyright-case-tracker/",
        "type": "html",
    },
    {
        "name": "Mishcon de Reya LLP",
        "url": "https://www.mishcon.com/generative-ai-intellectual-property-cases-and-policy-tracker",
        "type": "html",
    },
]

# ---------- Text helpers ----------
def clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def norm_caption(s: str) -> str:
    """Normalize captions for de-duplication (e.g., lower, strip punctuation/spaces)."""
    s = (s or "").lower()
    s = re.sub(r"[\.\,\-\–\—\(\)\[\]\:]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()

# ---------- Heuristics ----------
STATUS_KEYWORDS = [
    ("Judgment", r"\b(summary\s+judgment|judgment\s+entered|verdict|granted\s+(?:in\s+)?part)"),
    ("Dismissed", r"\b(dismiss(?:ed|al)|with\s+prejudice|without\s+prejudice)\b"),
    ("Injunction", r"\b(injunction|preliminary\s+injunction|permanent\s+injunction)\b"),
    ("Settled", r"\b(settle(?:d|ment))\b"),
    ("Class certified", r"\b(class\s+certification|certif(?:y|ied)\s+class)\b"),
    ("MDL/Transfer", r"\b(mdl|multi-?district|transferred|centralized)\b"),
    ("Stayed/Remand", r"\b(stay(?:ed)?|remand(?:ed)?)\b"),
    ("Recently filed", r"\b(complaint\s+filed|filed\s+(?:on|in)\s+\w+|\bnew\s+case)\b"),
]

def infer_status(text: str) -> str:
    t = (text or "").lower()
    for status, pat in STATUS_KEYWORDS:
        if re.search(pat, t, re.I):
            return status
    return "Open/Active"

def infer_outcome(text: str) -> str:
    t = (text or "").lower()
    if "fair use" in t and ("summary judgment" in t or "judgment" in t or "granted" in t):
        return "Fair use (SJ)"
    if re.search(r"\bsummary\s+judgment\b", t):
        return "Summary Judgment"
    if "injunction" in t:
        return "Injunction"
    if "dismiss" in t:
        return "Dismissal"
    if "settle" in t:
        return "Settlement"
    if "class cert" in t or "class certification" in t:
        return "Class Certification"
    if "verdict" in t:
        return "Verdict"
    return "Update"

def publisher_takeaway(text: str) -> str:
    """Music-publisher-focused takeaways when there is a clear ruling."""
    t = (text or "").lower()
    has_ruling = any(k in t for k in ["judgment", "dismiss", "injunction", "verdict", "settle", "class certific"])
    if not has_ruling:
        return ""
    if "fair use" in t and any(k in t for k in ["pirated", "torrent", "unauthorized", "scrape", "7 million"]):
        return ("Even if training is ruled fair use, dataset acquisition can still create liability. "
                "For music publishers, scrutinize provenance of audio datasets and any scraping of pirated files.")
    if "fair use" in t and "market" in t:
        return ("Courts weigh harm to the market for the original works more than any separate 'training license' market. "
                "Document concrete substitution/licensing displacement on your catalog.")
    if "injunction" in t:
        return ("Injunctions can limit model distribution or retraining; consider leverage for prospective relief and guardrails on future training.")
    if "dismiss" in t:
        return ("Complaints that don't connect copying to market harm risk dismissal. Tie training/outputs to measurable revenue impact on the catalog.")
    if "class certific" in t:
        return ("Class certification turns on commonality/predominance; heterogeneous catalogs can cut both ways.")
    if "settle" in t:
        return ("Settlements set practical benchmarks for training/output licenses even absent merits rulings.")
    if "verdict" in t:
        return ("Damages and apportionment frameworks are key for AI uses of sound recordings and compositions.")
    return ""

# ---------- Case extraction ----------
CAPTION_PAT = re.compile(
    r"\b([A-Z][A-Za-z0-9\.\-’'& ]+)\s+v\.?\s+([A-Z][A-Za-z0-9\.\-’'& ]+)\b"
)

def looks_like_case_title(s: str) -> bool:
    """Heuristic: contains 'v.' and not marketing/section fluff."""
    s = clean(s)
    if len(s) < 7: return False
    if " v " not in s.lower() and " v." not in s.lower(): return False
    if any(k in s.lower() for k in ["meet the team", "services", "subscribe", "event:", "training & development"]):
        return False
    return True

def extract_cases_from_html(name: str, url: str, html_text: str):
    soup = BeautifulSoup(html_text, "html.parser")
    items = []

    # Strategy:
    # - scan headings and anchor texts for captions (Foo v. Bar)
    # - grab nearby text for a short summary (same section/paragraph)
    # - always attach source URL
    candidates = []

    # Gather text nodes from headings and links
    for tag in soup.find_all(["h1","h2","h3","h4","a","strong","b","p","li"]):
        txt = clean(tag.get_text(" ", strip=True))
        if looks_like_case_title(txt):
            candidates.append((txt, tag))

    # Deduplicate nearby duplicates by text
    seen_local = set()
    for title, tag in candidates:
        key = norm_caption(title)
        if key in seen_local:
            continue
        seen_local.add(key)

        # local summary: next sibling paragraph/list item if any
        summary = ""
        nxt = tag.find_next_sibling(["p","li","div"])
        if nxt:
            stxt = clean(nxt.get_text(" ", strip=True))
            # keep short to avoid nav garbage
            if 20 <= len(stxt) <= 1000:
                summary = stxt

        # Sometimes link contains a more precise URL
        link = None
        if tag.name == "a" and tag.get("href"):
            href = tag.get("href").strip()
            if href.startswith("/"):
                link = url.rstrip("/") + href
            elif href.startswith("http"):
                link = href

        items.append({
            "title": title,
            "summary": summary or f"Referenced by {name}.",
            "source": name,
            "url": link or url,
        })

    return items

def fetch_url(u: str) -> str:
    for attempt in range(4):
        r = requests.get(u, timeout=45, headers={"User-Agent": "AI-Cases-Tracker/1.0"})
        if r.status_code == 200:
            return r.text
        if r.status_code in (429, 503):
            time.sleep(2 + attempt * 2)
            continue
        r.raise_for_status()
    raise RuntimeError(f"Failed to fetch {u}")

def gather_from_trackers():
    all_items = []
    for src in SOURCES:
        print(f"[scrape] {src['name']} -> {src['url']}", flush=True)
        html_text = fetch_url(src["url"])
        items = extract_cases_from_html(src["name"], src["url"], html_text)
        print(f"[scrape]  found {len(items)} candidates", flush=True)
        all_items.extend(items)

    # De-duplicate across sources by normalized caption
    dedup = {}
    for it in all_items:
        m = CAPTION_PAT.search(it["title"])
        caption = clean(m.group(0)) if m else clean(it["title"])
        key = norm_caption(caption)
        if key not in dedup:
            dedup[key] = {
                "title": caption,
                "headline": caption,  # refined below
                "date": "",
                "summary": it.get("summary",""),
                "source": it.get("source",""),
                "url": it.get("url",""),
                "outcome": "Update",
                "status": "Open/Active",
                "takeaway": ""
            }
        else:
            # prefer a non-empty summary and keep earliest source URL as canonical
            if it.get("summary") and len(it["summary"]) > len(dedup[key]["summary"]):
                dedup[key]["summary"] = it["summary"]
            if not dedup[key]["url"] and it.get("url"):
                dedup[key]["url"] = it["url"]
            # Record that we’ve seen it in multiple trackers
            if dedup[key]["source"] and dedup[key]["source"] != it.get("source",""):
                dedup[key]["source"] = "Multiple trackers"

    # Enrich headline / status / outcome / takeaway from summary text
    for k, v in dedup.items():
        text = f"{v['title']}. {v['summary']}"
        v["status"] = infer_status(text)
        v["outcome"] = infer_outcome(text)
        # Headline style like: "Bartz v. Anthropic – N.D. Cal rules AI training fair use."
        phrase = ""
        t = text.lower()
        if "fair use" in t and "summary judgment" in t:
            phrase = "rules AI training fair use"
        elif "fair use" in t and "judgment" in t:
            phrase = "rules AI training fair use"
        elif "injunction" in t:
            phrase = "issues injunction related to AI use"
        elif "dismiss" in t:
            phrase = "dismisses AI/IP claims"
        elif "settle" in t:
            phrase = "announces settlement in AI/IP dispute"
        elif "class cert" in t or "class certification" in t:
            phrase = "certifies class in AI/IP case"
        elif "verdict" in t:
            phrase = "returns verdict in AI/IP case"

        v["headline"] = f"{v['title']} – {phrase}." if phrase else v["title"]
        v["takeaway"] = publisher_takeaway(text)

    items = list(dedup.values())
    print(f"[scrape] total unique cases: {len(items)}", flush=True)
    return items

# ---------- Site HTML ----------
INDEX_HTML = """<!doctype html>
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
  .grid{display:grid; grid-template-columns:repeat(auto-fill, minmax(420px,1fr)); gap:14px}
  .card{background:var(--card); border:1px solid var(--line); border-radius:14px; padding:16px; display:flex; flex-direction:column; gap:10px}
  .title{font-weight:700; font-size:16px; line-height:1.35}
  .meta{font-size:12px; color:var(--muted); display:flex; gap:8px; align-items:center; flex-wrap:wrap}
  .pill{background:var(--pill); color:#fff; border-radius:999px; padding:3px 8px; font-size:11px; font-weight:600}
  .summary{font-size:14px; line-height:1.45}
  .footer{display:flex; justify-content:flex-end}
  .linkbtn{display:inline-block; padding:8px 10px; border-radius:10px; border:1px solid var(--line); background:#fff; font-size:13px; text-decoration:none}
  .linkbtn:hover{text-decoration:underline}
  .takeaway strong{font-weight:700}
</style>
</head>
<body>
  <h1>AI Court Cases Tracker</h1>
  <div class="sub">De-duplicated case summaries across McKool Smith, BakerHostetler, WIRED, and Mishcon trackers. Updates via GitHub Actions.</div>
  <div class="toolbar">
    <input id="q" type="search" placeholder="Filter by case, outcome, status, source…" aria-label="Filter"/>
    <select id="status" aria-label="Filter by status">
      <option value="">Status: All</option>
      <option>Recently filed</option>
      <option>Open/Active</option>
      <option>Judgment</option>
      <option>Dismissed</option>
      <option>Injunction</option>
      <option>Settled</option>
      <option>Class certified</option>
      <option>MDL/Transfer</option>
      <option>Stayed/Remand</option>
    </select>
    <select id="sort" aria-label="Sort">
      <option value="date">Sort: Date</option>
      <option value="title">Sort: Title</option>
      <option value="status">Sort: Status</option>
      <option value="source">Sort: Source</option>
    </select>
  </div>
  <div id="list" class="grid"></div>

<script>
async function load() {
  try {
    const res = await fetch('cases.json', {cache:'no-store'});
    if (!res.ok) throw new Error('Failed to load cases.json: ' + res.status);
    const data = await res.json();

    const list = document.getElementById('list');
    const q = document.getElementById('q');
    const sortSel = document.getElementById('sort');
    const statusSel = document.getElementById('status');

    function render(filter='', sortBy='title', statusFilter='') {
      const f = filter.toLowerCase();
      let items = data.filter(c => {
        const hay = (c.headline + ' ' + (c.outcome||'') + ' ' + (c.source||'') + ' ' + (c.summary||'') + ' ' + (c.status||'')).toLowerCase();
        const passText = !f || hay.includes(f);
        const passStatus = !statusFilter || (c.status||'').toLowerCase() === statusFilter.toLowerCase();
        return passText && passStatus;
      });

      items.sort((a,b)=>{
        if (sortBy === 'date') {
          const ax = a.date ? Date.parse(a.date) : 0;
          const bx = b.date ? Date.parse(b.date) : 0;
          return (bx - ax); // newest first
        } else {
          const ax=(a[sortBy]||'').toString().toLowerCase();
          const bx=(b[sortBy]||'').toString().toLowerCase();
          return ax.localeCompare(bx);
        }
      });

      list.innerHTML = '';
      items.forEach(c=>{
        const url = c.url || '#';
        const status  = c.status || 'Open/Active';
        const headline = (c.headline || c.title || 'Case');
        const src = c.source || '';
        const date = c.date ? new Date(c.date).toLocaleDateString() : '';
        const takeaway = (c.takeaway && c.takeaway.length)
          ? `<div class="summary takeaway"><strong>Key takeaway:</strong> ${c.takeaway}</div>`
          : '';

        const card = document.createElement('div');
        card.className = 'card';
        card.innerHTML = `
          <div class="title">${headline}</div>
          <div class="meta">
            <span class="pill">${status}</span>
            <span>${src}</span>
            <span>${date}</span>
          </div>
          <div class="summary">${c.summary || 'No summary available.'}</div>
          ${takeaway}
          <div class="footer">
            <a class="linkbtn" href="${url}" target="_blank" rel="noopener">View source →</a>
          </div>
        `;
        list.appendChild(card);
      });

      if (items.length === 0) {
        list.innerHTML = '<div class="card"><div class="title">No cases found</div><div class="summary">Try clearing filters or check back later.</div></div>';
      }
    }

    q.addEventListener('input', (e)=>render(e.target.value, sortSel.value, statusSel.value));
    sortSel.addEventListener('change', ()=>render(q.value, sortSel.value, statusSel.value));
    statusSel.addEventListener('change', ()=>render(q.value, sortSel.value, statusSel.value));
    render();
  } catch (e) {
    const list = document.getElementById('list');
    list.innerHTML = '<div class="card"><div class="title">Site is initializing</div><div class="summary">Could not load cases.json. If this is a fresh deploy, wait for the workflow to push docs/cases.json.</div></div>';
    console.error(e);
  }
}
load();
</script>
</body>
</html>
"""

def ensure_docs():
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(os.path.join(DOCS_DIR, ".nojekyll"), "w", encoding="utf-8") as f: f.write("")
    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f: f.write(INDEX_HTML)

def run():
    print("[tracker] scraping public trackers", flush=True)
    items = gather_from_trackers()
    ensure_docs()
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    print(f"[tracker] wrote {JSON_PATH} with {len(items)} items", flush=True)

if __name__ == "__main__":
    run()

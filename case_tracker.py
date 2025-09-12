#!/usr/bin/env python3
import os
import re
import json
import time
import requests
from urllib.parse import urlparse

# =========================
# CONFIG
# =========================
DOCS_DIR = "docs"
JSON_PATH = os.path.join(DOCS_DIR, "cases.json")

CL_DOCKETS = "https://www.courtlistener.com/api/rest/v4/dockets/"
CL_DEs     = "https://www.courtlistener.com/api/rest/v4/docket-entries/"

# Queries aimed at AI/IP training & related issues (broad but relevant)
QUERIES = [
    "training AND copyright",
    "dataset AND copyright",
    "ai AND copyright",
    "llm AND copyright",
    "ai AND right of publicity",
    "ai AND dmca",
]

# Runtime limits
MAX_PAGES_PER_QUERY = 3
DOCKET_ENTRIES_PER_CASE = 8
MAX_CASES_TOTAL = 250

# Auth
CL_API_TOKEN = os.environ.get("CL_API_TOKEN")

def http_headers():
    h = {
        "User-Agent": "AI-Court-Cases-Tracker (github.com/DerekAC7/AI-Court-Cases-Tracker)",
        "Accept": "application/json",
    }
    if CL_API_TOKEN:
        h["Authorization"] = f"Token {CL_API_TOKEN}"
    return h

# =========================
# HTTP HELPERS
# =========================
def fetch(url, params=None):
    """GET JSON with retries; explicit handling for 401/403/429."""
    for attempt in range(5):
        print(f"[http] GET {url} params={params} attempt={attempt+1}", flush=True)
        r = requests.get(url, headers=http_headers(), params=params, timeout=60)
        if r.status_code == 429:
            wait = 2 + attempt * 2
            print(f"[http] 429 rate-limited; sleeping {wait}s", flush=True)
            time.sleep(wait)
            continue
        if r.status_code == 401:
            body = (r.text or "")[:300]
            print(f"[http] 401 Unauthorized. Body: {body}", flush=True)
            raise RuntimeError("CourtListener 401 Unauthorized. Ensure CL_API_TOKEN repo secret is set and valid.")
        try:
            r.raise_for_status()
        except requests.HTTPError:
            print(f"[http] ERROR {r.status_code}: {r.text[:500]}", flush=True)
            raise
        print(f"[http] {r.status_code} ok", flush=True)
        return r.json()
    raise RuntimeError("HTTP retries exhausted")

# =========================
# TEXT/FORMAT HELPERS
# =========================
def clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def get_caption(d):
    # CL dockets use case_name (snake) or caseName (camel); caption may exist in some.
    return clean(d.get("case_name") or d.get("caseName") or d.get("caption") or "")

def get_court_slug(d):
    """
    Prefer 'court_id' if available. Otherwise parse slug from the 'court' URL,
    which looks like /api/rest/v4/courts/cand/
    """
    slug = d.get("court_id")
    if slug:
        return slug.lower()
    court_val = d.get("court")
    if isinstance(court_val, str) and court_val:
        # handle absolute or relative URL
        if court_val.startswith("http"):
            try:
                path = urlparse(court_val).path.strip("/").split("/")
                if path and path[-1]:
                    return path[-1].lower()
            except Exception:
                return None
        parts = court_val.strip("/").split("/")
        if parts and parts[-1]:
            return parts[-1].lower()
    return None

# Map CL court slugs to short names
COURT_MAP = {
    "cand": "N.D. Cal", "cacd": "C.D. Cal", "caed": "E.D. Cal", "casd": "S.D. Cal",
    "nysd": "S.D.N.Y.", "nyed": "E.D.N.Y.", "nysu": "Sup. Ct. N.Y.",
    "mad": "D. Mass", "ded": "D. Del", "ilnd": "N.D. Ill", "txnd": "N.D. Tex",
    "waed": "E.D. Wash", "wawd": "W.D. Wash", "vawd": "W.D. Va", "vaed": "E.D. Va",
    "flsd": "S.D. Fla", "flnd": "N.D. Fla", "gand": "N.D. Ga", "dcd": "D.D.C.",
    "ca9": "9th Cir.", "ca2": "2d Cir.", "cadc": "D.C. Cir.",
}
def court_short_from_slug(slug):
    if not slug:
        return ""
    return COURT_MAP.get(slug.lower(), slug.upper())

def status_from_text(t):
    tl = (t or "").lower()
    if any(k in tl for k in ["summary judgment","judgment entered","verdict","granted judgment"]): return "Judgment"
    if "dismiss" in tl: return "Dismissed"
    if "injunction" in tl: return "Injunction"
    if "settle" in tl: return "Settled"
    if "class certific" in tl: return "Class certified"
    if any(k in tl for k in ["mdl","transfer","centralized"]): return "MDL/Transfer"
    if any(k in tl for k in ["stayed","remand"]): return "Stayed/Remand"
    if any(k in tl for k in ["filed","complaint"]): return "Recently filed"
    return "Open/Active"

def outcome_short(t):
    m = re.search(
        r"(fair use|summary judgment|partial summary judgment|dismiss(ed)?|injunction|settle(d)?|class certification|md(l)?|transfer|stay(ed)?|remand|verdict|trial|damages)",
        t or "", re.I
    )
    return clean(m.group(0)).capitalize() if m else "Update"

def music_publisher_takeaway(t):
    tl = (t or "").lower()
    if not any(k in tl for k in ["judgment","dismiss","injunction","verdict","order","class certific","settle"]):
        return ""
    if "fair use" in tl and any(k in tl for k in ["pirated","torrent","unauthorized","scrape","infringing dataset","7 million"]):
        return ("Even if training is ruled fair use, dataset acquisition can still create liability. "
                "For music publishers, scrutinize dataset provenance and any scraping of pirated audio.")
    if "fair use" in tl and "market" in tl:
        return ("Courts weigh harm to the market for the original works more than a separate 'training license' market. "
                "Document concrete substitution/licensing displacement.")
    if "injunction" in tl:
        return ("Injunctions can restrict model distribution or retraining; evaluate leverage for prospective relief and guardrails on future training.")
    if "dismiss" in tl:
        return ("Complaints that don't connect copying to market harm risk dismissal. Tie training/outputs to measurable revenue impact on the catalog.")
    if "class certific" in tl:
        return ("Class certification turns on commonality/predominance; heterogeneous catalogs can cut both ways.")
    if "settle" in tl:
        return ("Settlements set practical benchmarks for training/output licenses even without merits rulings.")
    if "verdict" in tl or "damages" in tl:
        return ("Damages and apportionment frameworks will be key for AI uses of sound recordings and compositions.")
    return ""

def infer_headline_phrase(t):
    tl = (t or "").lower()
    if "fair use" in tl and ("summary judgment" in tl or "judgment" in tl or "granted" in tl):
        return "rules AI training fair use"
    if "injunction" in tl:
        return "issues injunction related to AI use"
    if "dismiss" in tl:
        return "dismisses AI/IP claims"
    if "class certific" in tl:
        return "certifies class in AI/IP case"
    if "settle" in tl:
        return "announces settlement in AI/IP dispute"
    if "verdict" in tl:
        return "returns verdict in AI/IP case"
    return ""

# =========================
# DATA ACCESS
# =========================
def docket_entries(docket_id, limit=DOCKET_ENTRIES_PER_CASE):
    """Fetch docket entries; skip gracefully on 401/403/404."""
    try:
        data = fetch(
            CL_DEs,
            {"docket": docket_id, "order_by": "date_filed desc", "page_size": limit}
        )
        return data.get("results", [])
    except requests.HTTPError as e:
        status = getattr(e.response, "status_code", None)
        if status in (401, 403, 404):
            print(f"[entries] skip docket {docket_id}: HTTP {status} (no permission or not available)", flush=True)
            return []
        raise
    except Exception as e:
        print(f"[entries] skip docket {docket_id}: {e}", flush=True)
        return []

def search_block(q):
    """Yield pages of dockets for a given query, following 'next' links."""
    print(f"[search] start: '{q}'", flush=True)
    url = CL_DOCKETS
    params = {"search": q, "order_by": "date_filed desc", "page_size": 50}
    pages = 0
    while url and pages < MAX_PAGES_PER_QUERY:
        data = fetch(url, params)
        pages += 1
        print(f"[search] page {pages} for '{q}' -> {len(data.get('results', []))} results", flush=True)
        yield data
        url = data.get("next")
        params = None  # subsequent requests follow absolute 'next' URL
        time.sleep(0.25)
    print(f"[search] done: '{q}' ({pages} page(s))", flush=True)

# =========================
# CORE GATHER
# =========================
def gather_from_dockets():
    items = []
    seen_ids = set()
    cases_collected = 0

    for q in QUERIES:
        print(f"[tracker] query: {q}", flush=True)
        for page in search_block(q):
            for d in page.get("results", []):
                if cases_collected >= MAX_CASES_TOTAL:
                    print("[tracker] hit MAX_CASES_TOTAL cap; stopping.", flush=True)
                    return items

                docket_id = d.get("id")
                if not docket_id or docket_id in seen_ids:
                    continue

                caption = get_caption(d)
                if not caption:
                    continue

                # entries may 401/403 — handled inside docket_entries()
                entries = docket_entries(docket_id)
                text_blob = " ".join([clean(e.get("description") or e.get("entry_text") or "") for e in entries])

                status  = status_from_text(text_blob) if entries else "Open/Active"
                outcome = outcome_short(text_blob)

                court_slug = get_court_slug(d)
                court_short = court_short_from_slug(court_slug)

                phrase = infer_headline_phrase(text_blob)
                if court_short and phrase:
                    headline = f"{caption} – {court_short} {phrase}."
                elif court_short:
                    headline = f"{caption} – {court_short}."
                else:
                    headline = caption

                # short summary from top docket entries if available
                top = [e for e in entries if clean(e.get("description",""))][:3]
                summary = ("On the docket: " + " ".join([clean(e["description"]) for e in top])) if top else "Docket retrieved from CourtListener/RECAP."
                takeaway = music_publisher_takeaway(text_blob)

                # Absolute URL handling: absolute_url is often a path
                abs_url = d.get("absolute_url") or ""
                if abs_url and not abs_url.startswith("http"):
                    abs_url = "https://www.courtlistener.com" + abs_url

                # Log and append
                print(f"[tracker]  + add: {headline}", flush=True)
                items.append({
                    "title": caption,
                    "headline": headline,
                    "date": d.get("date_filed") or "",
                    "summary": summary[:900],
                    "source": "CourtListener/RECAP",
                    "url": abs_url or f"https://www.courtlistener.com/docket/{docket_id}/",
                    "outcome": outcome if outcome else "Update",
                    "status": status,
                    "takeaway": takeaway
                })

                seen_ids.add(docket_id)
                cases_collected += 1

    print(f"[tracker] collected {len(items)} cases", flush=True)
    return items

# =========================
# SITE FILES
# =========================
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
  <div class="sub">De-duplicated case summaries from federal dockets (CourtListener/RECAP). Updates via GitHub Actions.</div>
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

    function render(filter='', sortBy='date', statusFilter='') {
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

# =========================
# MAIN
# =========================
def ensure_docs():
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(os.path.join(DOCS_DIR, ".nojekyll"), "w", encoding="utf-8") as f: f.write("")
    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f: f.write(INDEX_HTML)

def run():
    print("[tracker] starting CourtListener crawl", flush=True)
    items = gather_from_dockets()
    print(f"[tracker] collected {len(items)} cases", flush=True)
    ensure_docs()
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    print(f"[tracker] wrote {JSON_PATH}", flush=True)

if __name__ == "__main__":
    run()

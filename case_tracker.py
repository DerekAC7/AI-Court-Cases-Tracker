import os, re, json, time, requests

# ---------- CONFIG ----------
DOCS_DIR = "docs"
JSON_PATH = f"{DOCS_DIR}/cases.json"

# CourtListener v4 endpoints
CL_DOCKETS = "https://www.courtlistener.com/api/rest/v4/dockets/"
CL_DEs     = "https://www.courtlistener.com/api/rest/v4/docket-entries/"

# Broad AI+IP search terms (federal). We iterate combinations to be inclusive.
AI_TERMS = [
    "AI", "artificial intelligence", "generative", "LLM", "model", "training",
    "dataset", "diffusion", "deepfake", "replica", "scrape", "scraping"
]
IP_TERMS = [
    "copyright", "17 U.S.C.", "Lanham", "trademark", "right of publicity",
    "DMCA", "§ 106", "section 106", "sound recording", "musical works",
    "composition", "phonorecord", "license", "infringement"
]

# Limit pages per query so the job doesn't explode. Tune up/down as you like.
MAX_PAGES_PER_QUERY = 4
DOCKET_ENTRIES_PER_CASE = 8

CL_API_TOKEN = os.getenv("CL_API_TOKEN")

# ---------- HTTP ----------
def http_headers():
    h = {
        "User-Agent": "AI-Court-Cases-Tracker (github.com/DerekAC7/AI-Court-Cases-Tracker)",
        "Accept": "application/json",
    }
    if CL_API_TOKEN:
        h["Authorization"] = f"Token {CL_API_TOKEN}"
    return h

def fetch(url, params=None):
    for i in range(3):
        r = requests.get(url, params=params, headers=http_headers(), timeout=45)
        if r.status_code == 429:
            time.sleep(2 + i)
            continue
        if r.status_code == 401:
            raise RuntimeError("CourtListener 401 Unauthorized. Ensure CL_API_TOKEN repo secret is set.")
        r.raise_for_status()
        return r.json()
    r.raise_for_status()

# ---------- TEXT UTILS ----------
def clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def court_short(name):
    if not name: return ""
    x = name
    x = x.replace("United States District Court for the ","").replace("United States District Court, ","")
    x = x.replace("Northern District of California","N.D. Cal")
    x = x.replace("Southern District of New York","S.D.N.Y.")
    x = x.replace("Central District of California","C.D. Cal")
    x = x.replace("District of Delaware","D. Del")
    x = x.replace("District of Massachusetts","D. Mass")
    return x

def status_from_text(t):
    t = (t or "").lower()
    if any(k in t for k in ["summary judgment","judgment entered","verdict","granted judgment"]): return "Judgment"
    if "dismiss" in t: return "Dismissed"
    if "injunction" in t: return "Injunction"
    if "settle" in t: return "Settled"
    if "class certific" in t: return "Class certified"
    if any(k in t for k in ["mdl","transfer","centralized"]): return "MDL/Transfer"
    if any(k in t for k in ["stayed","remand"]): return "Stayed/Remand"
    if any(k in t for k in ["filed","complaint"]): return "Recently filed"
    return "Open/Active"

def outcome_short(t):
    m = re.search(
        r"(fair use|summary judgment|partial summary judgment|dismiss(ed)?|injunction|settle(d)?|class certification|md(l)?|transfer|stay(ed)?|remand|verdict|trial|damages)",
        t or "", re.I)
    return clean(m.group(0)).capitalize() if m else "Update"

def music_publisher_takeaway(t):
    t = (t or "").lower()
    if not any(k in t for k in ["judgment","dismiss","injunction","verdict","order","class certific"]):
        return ""
    if "fair use" in t and any(k in t for k in ["pirated","torrent","unauthorized","scrape"]):
        return ("Even if training is ruled fair use, dataset acquisition can still create liability. "
                "For music publishers, scrutinize provenance of audio datasets and any scraping of pirated files.")
    if "fair use" in t and "market" in t:
        return ("Courts weigh harm to the market for the original works more than a separate 'training license' market. "
                "Document concrete substitution or licensing displacement.")
    if "injunction" in t:
        return ("Injunctions can restrict model distribution or retraining; evaluate leverage for prospective relief "
                "and guardrails on future training.")
    if "dismiss" in t:
        return ("Complaints that don't connect copying to market harm risk dismissal. "
                "Tie training/outputs to measurable revenue impact on the catalog.")
    if "class certific" in t:
        return ("Class certification turns on commonality/predominance; heterogeneous catalogs can cut both ways.")
    if "settle" in t:
        return ("Settlements set practical benchmarks for training/output licenses even without merits rulings.")
    if "verdict" in t or "damages" in t:
        return ("Damages and apportionment frameworks will be key for AI uses of sound recordings and compositions.")
    return ""

def infer_headline_phrase(t):
    """Return the “… rules AI training fair use.” style fragment when we can."""
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
    return ""  # fallback to neutral “Update.”

# ---------- DATA GATHER ----------
def docket_entries(docket_id, limit=DOCKET_ENTRIES_PER_CASE):
    data = fetch(CL_DEs, {"docket": docket_id, "order_by": "date_filed desc", "page_size": limit})
    return data.get("results", [])

def is_ai_ip_related(caption, entries_text):
    t = (caption + " " + entries_text).lower()
    return (any(a in t for a in [x.lower() for x in AI_TERMS])
            and any(ip in t for ip in [x.lower() for x in IP_TERMS]))

def search_block(search_str):
    """Iterate the dockets endpoint with its 'search' parameter; yields results pages."""
    url = CL_DOCKETS
    params = {"search": search_str, "order_by": "date_filed desc", "page_size": 50}
    pages = 0
    while url and pages < MAX_PAGES_PER_QUERY:
        data = fetch(url, params)
        yield data
        url = data.get("next")
        params = None
        pages += 1
        time.sleep(0.25)

def gather_from_dockets():
    items = []
    seen_ids = set()

    # Build combined query strings like: "AI AND copyright", "training AND copyright", etc.
    COMBOS = []
    for a in AI_TERMS:
        for b in IP_TERMS:
            COMBOS.append(f"{a} AND {b}")

    for q in COMBOS:
        for page in search_block(q):
            for d in page.get("results", []):
                docket_id = d.get("id")
                if not docket_id or docket_id in seen_ids:
                    continue

                caption = clean(d.get("caption") or "")
                if not caption:
                    continue

                # Pull recent entries; then filter to AI+IP to cut noise
                entries = docket_entries(docket_id)
                text_blob = " ".join([clean(e.get("description") or e.get("entry_text") or "") for e in entries])
                if not is_ai_ip_related(caption, text_blob):
                    continue

                seen_ids.add(docket_id)

                status  = status_from_text(text_blob) if entries else "Open/Active"
                outcome = outcome_short(text_blob)
                court   = court_short(d.get("court","") or d.get("court_name",""))
                phrase  = infer_headline_phrase(text_blob)
                headline = caption + (f" – {court} " + (phrase + ".") if court and phrase else (f" – {court}." if court else ""))

                # Compose summary from the most recent entries
                top = [e for e in entries if clean(e.get("description",""))][:3]
                summary = ("On the docket: " + " ".join([clean(e["description"]) for e in top])) if top else "Docket retrieved from CourtListener/RECAP."

                takeaway = music_publisher_takeaway(text_blob)

                items.append({
                    "title": caption,
                    "headline": headline if headline else caption,
                    "date": d.get("date_filed") or "",
                    "summary": summary[:900],
                    "source": "CourtListener/RECAP",
                    "url": f"https://www.courtlistener.com/docket/{docket_id}/",
                    "outcome": outcome if outcome else "Update",
                    "status": status,
                    "takeaway": takeaway
                })
    # Dedup by normalized title
    uniq, seen_titles = [], set()
    for e in items:
        key = re.sub(r"\s+"," ", re.sub(r"[^\w\s]"," ", e["title"].lower()))
        if key in seen_titles: 
            continue
        seen_titles.add(key)
        uniq.append(e)
    return uniq

# ---------- SITE ----------
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
</style>
</head>
<body>
  <h1>AI Court Cases Tracker</h1>
  <div class="sub">Case summaries across federal dockets (CourtListener/RECAP). Updates via GitHub Actions.</div>
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
      <option value="title">Sort: Title</option>
      <option value="status">Sort: Status</option>
      <option value="source">Sort: Source</option>
      <option value="date">Sort: Date</option>
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
  const statusSel = document.getElementById('status');

  function render(filter='', sortBy='title', statusFilter='') {
    const f = filter.toLowerCase();
    let items = data.filter(c => {
      const hay = (c.title + ' ' + (c.outcome||'') + ' ' + (c.source||'') + ' ' + (c.summary||'') + ' ' + (c.status||'')).toLowerCase();
      const passText = !f || hay.includes(f);
      const passStatus = !statusFilter || (c.status||'').toLowerCase() === statusFilter.toLowerCase();
      return passText && passStatus;
    });

    items.sort((a,b)=>{
      const ax=(a[sortBy]||'').toString().toLowerCase();
      const bx=(b[sortBy]||'').toString().toLowerCase();
      return ax.localeCompare(bx);
    });

    list.innerHTML = '';
    items.forEach(c=>{
      const url = c.url || '#';
      const outcome = c.outcome || 'Update';
      const status  = c.status || 'Open/Active';
      const headline = (c.headline || c.title) + (outcome && !/(update)/i.test(outcome) ? '' : '');
      const src = c.source || '';
      const date = c.date ? new Date(c.date).toLocaleDateString() : '';
      const takeaway = c.takeaway ? `<div class="summary"><strong>Key takeaway:</strong> ${c.takeaway}</div>` : '';

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
  }

  q.addEventListener('input', (e)=>render(e.target.value, sortSel.value, statusSel.value));
  sortSel.addEventListener('change', ()=>render(q.value, sortSel.value, statusSel.value));
  statusSel.addEventListener('change', ()=>render(q.value, sortSel.value, statusSel.value));
  render();
}
load();
</script>
</body>
</html>
"""

# ---------- MAIN ----------
def ensure_docs():
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(os.path.join(DOCS_DIR, ".nojekyll"), "w", encoding="utf-8") as f: f.write("")
    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f: f.write(INDEX_HTML)

def run():
    items = gather_from_dockets()
    ensure_docs()
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    print(f"Wrote {len(items)} cases to {JSON_PATH}")

if __name__ == "__main__":
    run()

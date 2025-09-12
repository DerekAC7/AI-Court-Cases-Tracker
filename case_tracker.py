import os, re, json, hashlib, requests
from datetime import datetime, timedelta
from urllib.parse import urlencode

# ====== CONFIG ======
REPO_OWNER = "DerekAC7"
REPO_NAME  = "AI-Court-Cases-Tracker"
TOKEN = os.getenv("PERSONAL_ACCESS_TOKEN")  # mapped from secrets.PAT_TOKEN
DOCS_DIR = "docs"
JSON_PATH = f"{DOCS_DIR}/cases.json"
TIMEOUT = 40

# CourtListener/RECAP endpoints (public; no auth needed)
CL_SEARCH = "https://www.courtlistener.com/api/rest/v4/search/"     # search across case law + PACER metadata
CL_DOCKETS = "https://www.courtlistener.com/api/rest/v4/dockets/"   # docket objects
CL_DEs = "https://www.courtlistener.com/api/rest/v4/docket-entries/"

# Parties we care about (expand as needed)
TARGET_PARTIES = [
    "OpenAI", "Anthropic", "Meta", "Midjourney", "Stability AI", "Stability",
    "Suno", "Udio", "Reddit", "Disney", "Getty", "New York Times",
    "Authors Guild", "Universal Music", "UMG", "Warner Music", "Sony Music"
]

AI_KEYWORDS = [
    "AI","artificial intelligence","generative","LLM","model","training",
    "dataset","copyright","diffusion","watermark","deepfake","replica"
]

# ====== helpers ======
def gh_headers():
    if not TOKEN: raise RuntimeError("Missing token (map secrets.PAT_TOKEN to PERSONAL_ACCESS_TOKEN).")
    return {"Authorization": f"token {TOKEN}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "ai-litigation-tracker-bot"}

def clean(s): return re.sub(r"\s+"," ",(s or "").strip())

def status_from_text(t):
    t = (t or "").lower()
    if any(x in t for x in ["summary judgment","judgment entered","granted judgment","verdict"]): return "Judgment"
    if any(x in t for x in ["dismissed","dismissal","12(b)"]): return "Dismissed"
    if any(x in t for x in ["preliminary injunction","permanent injunction","injunction"]): return "Injunction"
    if any(x in t for x in ["settlement","settled"]): return "Settled"
    if any(x in t for x in ["class certified","class certification"]): return "Class certified"
    if any(x in t for x in ["mdl","transferred","transfer order","centralized"]): return "MDL/Transfer"
    if any(x in t for x in ["stayed","remand"]): return "Stayed/Remand"
    return "Open/Active"

def outcome_short(t):
    m = re.search(r"(fair use|summary judgment|partial summary judgment|dismiss(ed)?|prelim(inary)? injunction|injunction|"
                  r"settle(d)?|class certification|md(l)?|transfer|stay(ed)?|remand|verdict|trial|damages)", t or "", re.I)
    return clean(m.group(0)).capitalize() if m else "Update"

def music_publisher_takeaway(text):
    t = (text or "").lower()
    # Only emit if we detect an actual ruling-ish signal
    if not any(k in t for k in ["judgment","dismiss","injunction","verdict","order","class certific"]):
        return ""
    if "fair use" in t and any(k in t for k in ["pirated","torrent","unauthorized","7 million","scrape"]):
        return ("Even if training is ruled fair use, dataset acquisition can still create liability. "
                "For music publishers, scrutinize provenance of audio datasets and any scraping of pirated files.")
    if "fair use" in t and "market" in t:
        return ("Courts weigh harm to the market for the original works more than a separate 'training license' market. "
                "Publishers should document concrete substitution or licensing displacement.")
    if "injunction" in t:
        return ("Injunctions can restrict model distribution or retraining. "
                "Publishers should evaluate leverage for prospective relief and guardrails on future training.")
    if "dismiss" in t:
        return ("Complaints that don't connect copying to market harm risk dismissal. "
                "Tie training/outputs to measurable revenue impact on the catalog.")
    if "class certific" in t:
        return ("Class certification hinges on commonality/predominance; heterogeneous catalogs can cut both ways. "
                "Watch whether work-by-work issues defeat class treatment.")
    if "settle" in t:
        return ("Parties are resolving AI/IP disputes without merits rulings—use settlement patterns to set benchmarks "
                "for training and output licenses.")
    if "verdict" in t or "damages" in t:
        return ("Damages frameworks and attribution will be key. "
                "Model statutory vs. actual damages and apportionment for AI uses.")
    return ""

def fetch(url, params=None):
    r = requests.get(url, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()

def court_short(name):
    # Try to compress common district names
    if not name: return ""
    x = name.replace("District Court for the ","").replace("District Court, ","")
    x = x.replace("United States ","").replace("U.S. ","")
    x = x.replace("Northern District of California","N.D. Cal").replace("Southern District of New York","S.D.N.Y.")
    x = x.replace("Central District of California","C.D. Cal").replace("District of Delaware","D. Del")
    x = x.replace("District of Massachusetts","D. Mass")
    return x

def build_query():
    # party:(OpenAI OR Anthropic ... ) AND (AI keywords)
    party_q = " OR ".join([f'"{p}"' for p in TARGET_PARTIES])
    kw_q = " OR ".join([f'"{k}"' for k in AI_KEYWORDS])
    # CourtListener v4 search params:
    # q=, type=dockets|opinions|everything, order_by, date filters, etc.
    return {
        "q": f"({party_q}) ({kw_q})",
        "type": "dockets",
        "page_size": 50,  # per page
        "order_by": "dateFiled desc"
    }

def latest_entries_for_docket(docket_id, limit=6):
    data = fetch(CL_DEs, {"docket": docket_id, "page_size": limit, "order_by": "date_filed desc"})
    return data.get("results", [])

def search_dockets():
    results = []
    params = build_query()
    url = CL_SEARCH
    while True:
        data = fetch(url, params)
        for row in data.get("results", []):
            if row.get("result_type") != "docket": continue
            d = row.get("docket") or {}
            # Basic fields
            caption = clean(d.get("caption") or row.get("caseName") or "")
            if not caption: continue
            court = court_short(d.get("court_name") or row.get("court") or "")
            docket_id = d.get("id") or row.get("id")
            docket_num = d.get("docket_number") or row.get("docketNumber") or ""
            filed = d.get("date_filed") or row.get("dateFiled") or ""
            # Pull recent entries to infer status/outcome
            entries = latest_entries_for_docket(docket_id)
            text_blob = " ".join([clean(x.get("description") or x.get("entry_text") or "") for x in entries])
            status = status_from_text(text_blob) if entries else "Open/Active"
            outcome = outcome_short(text_blob)
            takeaway = music_publisher_takeaway(text_blob)
            headline = caption + (f" – {court}" if court else "")
            # Build summary from most recent notable entries
            top = [e for e in entries if len(clean(e.get("description",""))) > 0][:3]
            if top:
                bullets = "; ".join([clean(e["description"]) for e in top])
                summary = f"Recent docket activity: {bullets}"
            else:
                summary = "Docket retrieved from CourtListener/RECAP."

            results.append({
                "title": caption,
                "headline": headline,
                "date": filed or "",
                "summary": summary[:900],
                "source": "CourtListener/RECAP",
                "url": f"https://www.courtlistener.com/docket/{docket_id}/",
                "outcome": outcome,
                "status": status,
                "takeaway": takeaway
            })
        # pagination
        next_url = data.get("next")
        if not next_url: break
        url, params = next_url, None
    return results

def normalize_case_key(title):
    t = title.lower()
    t = re.sub(r"\bvs\.?\b","v.", t)
    t = re.sub(r"\s+v\.\s+"," v. ", t)
    t = re.sub(r"[^\w\s]"," ", t)
    t = re.sub(r"\s+"," ", t).strip()
    return t

def unique(docs):
    seen=set(); out=[]
    for e in docs:
        k = normalize_case_key(e["title"])
        if k in seen: continue
        seen.add(k); out.append(e)
    return out

# ====== site writer ======
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
  <div class="sub">Case summaries across multiple trackers. Updates via GitHub Actions.</div>
  <div class="toolbar">
    <input id="q" type="search" placeholder="Filter by case, outcome, source…" aria-label="Filter"/>
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
      const headline = (c.headline || c.title) + ' – ' + outcome + '.';
      const src = c.source || '';
      const takeaway = c.takeaway ? `<div class="summary"><strong>Key takeaway:</strong> ${c.takeaway}</div>` : '';

      const card = document.createElement('div');
      card.className = 'card';
      card.innerHTML = `
        <div class="title">${headline}</div>
        <div class="meta">
          <span class="pill">${status}</span>
          <span>${src}</span>
          <span>${c.date ? new Date(c.date).toLocaleDateString() : ''}</span>
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

# ====== main ======
def ensure_docs():
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(os.path.join(DOCS_DIR, ".nojekyll"), "w", encoding="utf-8") as f: f.write("")
    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f: f.write(_INDEX_HTML)

def run():
    # 1) search structured dockets from CourtListener
    items = search_dockets()
    items = unique(items)
    # 2) write site artifacts
    ensure_docs()
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    print(f"Wrote {JSON_PATH} with {len(items)} cases.")

if __name__ == "__main__":
    run()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Court Cases Tracker
- Scrapes four public trackers (McKool, BakerHostetler, WIRED, Mishcon)
- Extracts real case captions "X v. Y"
- Filters by AI/IP-training context to avoid junk
- Compresses long party lists ("et al.") for readability
- Pulls case reference numbers when available
- Infers status/outcome and adds Key takeaway only for rulings
- De-duplicates across sources
- Writes docs/index.html and docs/cases.json
"""
import os, re, json, time, html
import requests
from bs4 import BeautifulSoup

DOCS_DIR = "docs"
JSON_PATH = os.path.join(DOCS_DIR, "cases.json")

SOURCES = [
    {"name": "McKool Smith", "url": "https://www.mckoolsmith.com/newsroom-ailitigation"},
    {"name": "BakerHostetler", "url": "https://www.bakerlaw.com/services/artificial-intelligence-ai/case-tracker-artificial-intelligence-copyrights-and-class-actions/"},
    {"name": "WIRED", "url": "https://www.wired.com/story/ai-copyright-case-tracker/"},
    {"name": "Mishcon de Reya LLP", "url": "https://www.mishcon.com/generative-ai-intellectual-property-cases-and-policy-tracker"},
]

HEADERS = {"User-Agent": "AI-Cases-Tracker/2.0 (+github pages bot)"}

# ---------- regexes ----------
CAPTION_PAT = re.compile(r"\b([A-Z][A-Za-z0-9\.\-’'& ]+)\s+v\.?\s+([A-Z][A-Za-z0-9\.\-’'& ]+)\b")
CASE_NO_PAT = re.compile(r"\b(\d{1,2}:\d{2}-cv-\d{4,6}[A-Za-z\-]*|No\.\s?[A-Za-z0-9\-\.:]+|Case\s?(?:No\.|#)\s?[A-Za-z0-9\-:]+|Claim\s?No\.\s?[A-Za-z0-9\-]+)\b", re.I)

AI_CONTEXT_PAT = re.compile(
    r"\b(ai|artificial intelligence|gen(?:erative)? ai|llm|model|training|dataset|copyright|dmca|right of publicity|digital replica|source code)\b",
    re.I,
)

# status / outcome inference
STATUS_RULES = [
    ("Judgment", r"\b(summary\s+judgment|judgment\s+entered|granted\s+summary|verdict)\b"),
    ("Dismissed", r"\b(dismiss(?:al|ed)|with\s+prejudice|without\s+prejudice)\b"),
    ("Injunction", r"\b(preliminary|permanent)\s+injunction\b|\binjunction\b"),
    ("Settled", r"\b(settle(?:d|ment))\b"),
    ("Class certified", r"\b(class\s+certification|certif(?:y|ied)\s+class)\b"),
    ("MDL/Transfer", r"\b(mdl|multi-?district|transferred|centralized)\b"),
    ("Stayed/Remand", r"\b(stay(?:ed)?|remand(?:ed)?)\b"),
    ("Recently filed", r"\b(complaint\s+filed|filed\s+on|new\s+case)\b"),
]

# tailor headlines
HEADLINE_KEYS = [
    ("rules AI training fair use", r"\bfair\s+use\b.*\b(summary\s+judgment|judgment)\b"),
    ("issues injunction related to AI use", r"\binjunction\b"),
    ("dismisses AI/IP claims", r"\bdismiss"),
    ("announces settlement in AI/IP dispute", r"\bsettle"),
    ("certifies class in AI/IP case", r"\bclass\s+cert"),
    ("returns verdict in AI/IP case", r"\bverdict\b"),
]

# known music-publisher focused takeaways
def music_takeaway(text: str) -> str:
    t = text.lower()
    if "fair use" in t and ("pirated" in t or "torrent" in t or "unauthorized" in t or "7 million" in t or "shadow library" in t):
        return ("Even if training is ruled fair use, acquisition of pirated datasets can still create liability. "
                "For music publishers, scrutinize provenance of audio datasets and any scraping of leaked files.")
    if "fair use" in t and "market" in t:
        return ("Courts weigh harm to the market for the original works more than any separate 'training-license' market. "
                "Document concrete substitution/licensing displacement on your catalog.")
    if "injunction" in t:
        return ("Injunctions can limit model distribution or future retraining—leverage for prospective relief and guardrails on music ingestion.")
    if "dismiss" in t:
        return ("Complaints that don’t connect copying to cognizable market harm risk dismissal. "
                "Tie training/outputs to measurable revenue impact and licensing loss.")
    if "settle" in t:
        return ("Settlements set practical ranges for training/output licenses even without merits rulings—useful for negotiation benchmarks.")
    if "verdict" in t:
        return ("Damages frameworks and apportionment will shape payouts for sound recordings and compositions in AI contexts.")
    return ""

# --------- helpers ----------
def clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def fetch(url: str) -> str:
    for i in range(4):
        r = requests.get(url, timeout=45, headers=HEADERS)
        if r.status_code == 200:
            return r.text
        if r.status_code in (429, 503):
            time.sleep(2 + 2 * i)
            continue
        r.raise_for_status()
    raise RuntimeError(f"Failed to fetch {url}")

def extract_visible_text(html_text: str) -> str:
    soup = BeautifulSoup(html_text, "html.parser")
    for bad in soup(["script","style","noscript","svg","nav","header","footer","form","aside"]):
        bad.decompose()
    txt = soup.get_text(" ", strip=True)
    txt = html.unescape(txt)
    return clean(txt)

def window(text: str, start: int, end: int, radius: int = 360) -> str:
    a = max(0, start - radius)
    b = min(len(text), end + radius)
    return clean(text[a:b])

def compress_caption(caption: str) -> str:
    """
    Turn massive party lists into 'Foo et al. v Bar' forms.
    E.g., "(1) A (2) B (3) C v (1) OpenAI ... " -> "A et al. v OpenAI et al."
    """
    cap = caption
    # remove numbering artifacts like "(1) " etc.
    cap = re.sub(r"\(\d+\)\s*", "", cap)
    # Split sides
    vm = re.search(r"\s+v\.?\s+", cap)
    if not vm:
        return clean(cap)
    left = cap[:vm.start()]
    right = cap[vm.end():]
    def first_party(side: str) -> str:
        # split by commas/ & / and / ;
        parts = re.split(r"\s*,\s*| & | and |;|\s{2,}", side)
        lead = clean(parts[0]) if parts and parts[0] else clean(side)
        # trim trailing org punctuation
        lead = re.sub(r"[,;]+$", "", lead).strip()
        return lead
    def has_many(side: str) -> bool:
        return bool(re.search(r"\bet\.?\s*al\.?|,|\band\b|&", side, re.I)) or len(side) > 60
    L = first_party(left)
    R = first_party(right)
    L_more = " et al." if has_many(left) else ""
    R_more = " et al." if has_many(right) else ""
    return f"{L}{L_more} v {R}{R_more}"

def infer_status(text: str) -> str:
    for status, pat in STATUS_RULES:
        if re.search(pat, text, re.I):
            return status
    return "Open/Active"

def infer_outcome(text: str) -> str:
    t = text.lower()
    if "fair use" in t and re.search(r"\bsummary\s+judgment|\bjudgment\b", t):
        return "Fair use (SJ)"
    if re.search(r"\bsummary\s+judgment\b", t): return "Summary Judgment"
    if "injunction" in t: return "Injunction"
    if "dismiss" in t: return "Dismissal"
    if "settle" in t: return "Settlement"
    if "class cert" in t or "class certification" in t: return "Class Certification"
    if "verdict" in t: return "Verdict"
    return "Update"

def headline_for(text: str, caption: str) -> str:
    for phrase, pat in HEADLINE_KEYS:
        if re.search(pat, text, re.I):
            return f"{caption} – {phrase}."
    return caption

def gather_from_sources():
    items = []
    for src in SOURCES:
        print(f"[scrape] {src['name']} -> {src['url']}", flush=True)
        html_text = fetch(src["url"])
        full = extract_visible_text(html_text)

        for m in CAPTION_PAT.finditer(full):
            cap = clean(m.group(0))
            ctx = window(full, m.start(), m.end(), radius=400)

            # require AI/IP context in the neighborhood to cut noise
            if not AI_CONTEXT_PAT.search(ctx):
                continue

            caption_short = compress_caption(cap)
            # case number if present near
            case_no_match = CASE_NO_PAT.search(ctx)
            case_no = clean(case_no_match.group(0)) if case_no_match else ""

            text_for_rules = f"{caption_short}. {ctx}"
            status = infer_status(text_for_rules)
            outcome = infer_outcome(text_for_rules)
            takeaway = music_takeaway(text_for_rules) if status in {"Judgment","Dismissed","Injunction","Settled","Class certified","Verdict"} else ""

            item = {
                "title": caption_short,
                "headline": headline_for(text_for_rules, caption_short),
                "date": "",  # trackers rarely publish exact court dates consistently
                "summary": ctx[:900],
                "source": src["name"],
                "url": src["url"],
                "outcome": outcome,
                "status": status,
                "takeaway": takeaway,
                "case_ref": case_no,
            }
            items.append(item)
        print(f"[scrape]   now {len(items)} extracted", flush=True)

    # de-duplicate by normalized caption (title + case_ref if present)
    def norm_key(it):
        t = re.sub(r"[^a-z0-9]+", " ", it["title"].lower()).strip()
        cref = it.get("case_ref","").lower()
        return f"{t}|{cref}"
    dedup = {}
    for it in items:
        k = norm_key(it)
        # prefer longer summary / stronger source ordering
        score = len(it.get("summary",""))
        if k not in dedup or score > len(dedup[k].get("summary","")):
            dedup[k] = it

    items2 = list(dedup.values())
    # sort by title
    items2.sort(key=lambda x: x["title"].lower())
    print(f"[scrape] total unique cases: {len(items2)}", flush=True)
    return items2

# ---------- site HTML ----------
INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>AI Court Cases Tracker</title>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<style>
  :root { --fg:#0f172a; --muted:#475569; --bg:#ffffff; --card:#f8fafc; --line:#e2e8f0; --pill:#0ea5e9; --pill2:#7c3aed; }
  *{box-sizing:border-box}
  html,body{margin:0;padding:0}
  body{font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif; color:var(--fg); background:var(--bg); padding:24px;}
  h1{margin:0 0 6px 0; font-size:26px; font-weight:800}
  .sub{color:var(--muted); margin-bottom:16px; font-size:14px}
  .toolbar{display:flex; gap:12px; margin:12px 0 18px; flex-wrap:wrap}
  input,select{padding:10px 12px; border:1px solid var(--line); border-radius:10px; font-size:14px}
  .grid{display:grid; grid-template-columns:repeat(auto-fill, minmax(420px,1fr)); gap:14px}
  .card{background:var(--card); border:1px solid var(--line); border-radius:14px; padding:16px; display:flex; flex-direction:column; gap:10px}
  .title{font-weight:800; font-size:16px; line-height:1.35}
  .meta{font-size:12px; color:var(--muted); display:flex; gap:8px; align-items:center; flex-wrap:wrap}
  .pill{background:var(--pill); color:#fff; border-radius:999px; padding:3px 8px; font-size:11px; font-weight:700}
  .pill2{background:var(--pill2); color:#fff; border-radius:999px; padding:3px 8px; font-size:11px; font-weight:700}
  .summary{font-size:14px; line-height:1.45}
  .footer{display:flex; justify-content:space-between; align-items:center}
  .linkbtn{display:inline-block; padding:8px 10px; border-radius:10px; border:1px solid var(--line); background:#fff; font-size:13px; text-decoration:none}
  .linkbtn:hover{text-decoration:underline}
  .takeaway strong{font-weight:800}
  .ref{font-size:12px; color:var(--muted)}
</style>
</head>
<body>
  <h1>AI Court Cases Tracker</h1>
  <div class="sub">De-duplicated case summaries across McKool Smith, BakerHostetler, WIRED, and Mishcon. Filter by status; rulings include music-publisher-focused key takeaways.</div>
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
      <option value="date">Sort: Date</option>
      <option value="status">Sort: Status</option>
      <option value="source">Sort: Source</option>
    </select>
  </div>
  <div id="list" class="grid"></div>

<script>
async function load() {
  const list = document.getElementById('list');
  try {
    const res = await fetch('cases.json', {cache:'no-store'});
    if (!res.ok) throw new Error('Failed to load cases.json: ' + res.status);
    const data = await res.json();

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
          return (bx - ax);
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
        const cref = c.case_ref ? `<span class="ref">Case ref: ${c.case_ref}</span>` : '';
        const takeaway = (c.takeaway && c.takeaway.length)
          ? `<div class="summary takeaway"><strong>Key takeaway:</strong> ${c.takeaway}</div>`
          : '';
        const outcomePill = c.outcome && c.outcome !== 'Update' ? `<span class="pill2">${c.outcome}</span>` : '';

        const card = document.createElement('div');
        card.className = 'card';
        card.innerHTML = `
          <div class="title">${headline}</div>
          <div class="meta">
            <span class="pill">${status}</span>
            ${outcomePill}
            <span>${src}</span>
            <span>${date}</span>
            ${cref}
          </div>
          <div class="summary">${c.summary || 'No summary available.'}</div>
          ${takeaway}
          <div class="footer">
            <span></span>
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
    list.innerHTML = '<div class="card"><div class="title">Site is initializing</div><div class="summary">Could not load <code>cases.json</code>. Verify the Action wrote <code>docs/cases.json</code> and Pages is set to main /docs.</div></div>';
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
    # Ensure GitHub Pages builds from /docs without Jekyll
    with open(os.path.join(DOCS_DIR, ".nojekyll"), "w", encoding="utf-8") as f:
        f.write("")
    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(INDEX_HTML)

def run():
    print("[tracker] scraping four public trackers", flush=True)
    items = gather_from_sources()
    ensure_docs()
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    print(f"[tracker] wrote {JSON_PATH} with {len(items)} items", flush=True)

if __name__ == "__main__":
    run()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Court Cases Tracker — pulls latest McKool Smith weekly edition and 3 other public trackers,
merges & de-dupes, and writes docs/index.html + docs/cases.json for GitHub Pages.

Sources:
  - McKool Smith (ALWAYS fetch the latest edition; parse numbered case blocks)
  - BakerHostetler
  - WIRED
  - Mishcon de Reya

Output:
  - docs/index.html (UI)
  - docs/cases.json (data)

Usage:
  pip install requests beautifulsoup4
  python case_tracker.py
"""

import os, re, time, json, html
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

# -----------------------
# Config
# -----------------------

DOCS_DIR = "docs"
JSON_PATH = os.path.join(DOCS_DIR, "cases.json")

SOURCES = [
    {"name": "McKool Smith", "url": "https://www.mckoolsmith.com/newsroom-ailitigation"},
    {"name": "BakerHostetler", "url": "https://www.bakerlaw.com/services/artificial-intelligence-ai/case-tracker-artificial-intelligence-copyrights-and-class-actions/"},
    {"name": "WIRED", "url": "https://www.wired.com/story/ai-copyright-case-tracker/"},
    {"name": "Mishcon de Reya LLP", "url": "https://www.mishcon.com/generative-ai-intellectual-property-cases-and-policy-tracker"},
]

HEADERS = {"User-Agent": "AI-Cases-Tracker/9.1 (+GitHub Actions)"}

# Prefer McKool text first (it’s the curated weekly digest),
# then Mishcon, then Baker, then WIRED.
SOURCE_PREFERENCE = ["McKool Smith", "Mishcon de Reya LLP", "BakerHostetler", "WIRED"]

AI_IP_PARTIES = [
    "OpenAI","Anthropic","Meta","Google","Alphabet","Midjourney","Stability AI","Suno","Udio",
    "Perplexity","Cohere","Nvidia","Ross Intelligence","Thomson Reuters","Getty","Disney",
    "Universal","UMG","Warner","Sony","Authors Guild","New York Times","Reddit","Databricks",
    "LAION","GitHub","Microsoft","Bloomberg","IGN","Ziff Davis","Everyday Health","Dow Jones",
    "NY Post","Center for Investigative Reporting","Canadian Broadcasting Corporation","Radio-Canada",
    "Warner Bros. Discovery","Uncharted Labs","Paramount","Sony"
]

CAPTION_PAT = re.compile(r"\b([A-Z][A-Za-z0-9\.\-’'& ]{1,90})\s+v\.?\s+([A-Z][A-Za-z0-9\.\-’'& ]{1,90})\b")
CASE_NO_PAT = re.compile(r"\b(\d{1,2}:\d{2}-cv-\d{4,6}[A-Za-z\-]*|No\.\s?[A-Za-z0-9\-\.:]+|Case\s?(?:No\.|#)\s?[A-Za-z0-9\-:]+|Claim\s?No\.\s?[A-Za-z0-9\-]+)\b", re.I)
AI_CONTEXT_PAT = re.compile(
    r"\b(ai|artificial intelligence|gen(?:erative)? ai|llm|model|training|dataset|copyright|dmca|"
    r"right of publicity|digital replica|scrap(?:e|ing)|music|recordings?|publisher|labels?|lyrics|headnotes|r\.?a\.?g|rag)\b",
    re.I,
)
JUNK_HEADINGS_PAT = re.compile(
    r"^(case updates|current edition|our professionals|disclaimer|training & development|"
    r"a b c d e f g h i j k l m n o p q r s t u v w x y z|interactive entertainment|"
    r"blockchain, crypto and digital assets|mishcon purpose|philanthropic strategy|supply chain advice)\b",
    re.I
)

# -----------------------
# UI (index.html) — HTML with bold summaries
# -----------------------

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
  .summary{font-size:14px; line-height:1.5}
  .summary b{font-weight:800}
  .footer{display:flex; justify-content:space-between; align-items:center}
  .linkbtn{display:inline-block; padding:8px 10px; border-radius:10px; border:1px solid var(--line); background:#fff; font-size:13px; text-decoration:none}
  .linkbtn:hover{text-decoration:underline}
  .ref{font-size:12px; color:var(--muted)}
</style>
</head>
<body>
  <h1>AI Court Cases Tracker</h1>
  <div class="sub">Latest weekly summaries from McKool Smith plus cross-checks with BakerHostetler, WIRED, and Mishcon.</div>

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
    </select>
    <select id="sort" aria-label="Sort">
      <option value="title">Sort: Title</option>
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
        const ax=(a[sortBy]||'').toString().toLowerCase();
        const bx=(b[sortBy]||'').toString().toLowerCase();
        return ax.localeCompare(bx);
      });

      list.innerHTML = '';
      items.forEach(c=>{
        const url = c.url || '#';
        const status  = c.status || 'Open/Active';
        const headline = (c.headline || c.title || 'Case');
        const src = c.source || '';
        const cref = c.case_ref ? `<span class="ref">Case ref: ${c.case_ref}</span>` : '';
        const hasEmbeddedKT = (c.summary||"").toLowerCase().includes("<b>key takeaway:</b>");
        const ktInline = (!hasEmbeddedKT && c.takeaway)
          ? `<div class="summary"><b>Key takeaway:</b> ${c.takeaway}</div>`
          : '';

        const card = document.createElement('div');
        card.className = 'card';
        card.innerHTML = `
          <div class="title">${headline}</div>
          <div class="meta">
            <span class="pill">${status}</span>
            ${c.outcome && c.outcome !== 'Update' ? `<span class="pill2">${c.outcome}</span>` : ''}
            <span>${src}</span>
            ${cref}
          </div>
          <div class="summary">${c.summary || '<b>Summaries:</b> No summary available.'}</div>
          ${ktInline}
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

# -----------------------
# HTTP
# -----------------------

def fetch(url: str) -> str:
    for i in range(4):
        r = requests.get(url, headers=HEADERS, timeout=45)
        if r.status_code == 200:
            return r.text
        if r.status_code in (429, 503):
            time.sleep(2 + 2*i); continue
        r.raise_for_status()
    raise RuntimeError(f"Failed to fetch {url}")

# -----------------------
# Generic helpers
# -----------------------

def looks_like_junk(title: str, ctx: str) -> bool:
    if JUNK_HEADINGS_PAT.match((title or "").strip()):
        return True
    low = (f"{title or ''} {ctx or ''}").lower()
    if "our professionals" in low:
        return True
    if re.search(r"\bA B C D E F G\b", title or ""):
        return True
    if "background" == (title or "").strip().lower():
        return True
    if "case updates" in (title or "").lower():
        return True
    return False

def compress_caption(caption: str) -> str:
    cap = re.sub(r"\(\d+\)\s*", "", caption or "")
    m = re.search(r"\s+v\.?\s+", cap)
    if not m: return cap.strip()
    left, right = cap[:m.start()], cap[m.end():]
    def first_party(side: str) -> str:
        parts = re.split(r"\s*,\s*| & | and |;|\s{2,}", side)
        lead = parts[0].strip() if parts and parts[0] else side.strip()
        return re.sub(r"[,;]+$", "", lead)
    def many(side: str) -> bool:
        return bool(re.search(r"\bet\.?\s*al\.?|,|\band\b|&", side, re.I)) or len(side) > 60
    L = first_party(left) + (" et al." if many(left) else "")
    R = first_party(right) + (" et al." if many(right) else "")
    L = re.sub(r"(et al\.)\s*et al\.$", r"\1", L, flags=re.I)
    R = re.sub(r"(et al\.)\s*et al\.$", r"\1", R, flags=re.I)
    # strip trailing "Background"
    L = re.sub(r"\s*\bbackground\b\s*$", "", L, flags=re.I)
    R = re.sub(r"\s*\bbackground\b\s*$", "", R, flags=re.I)
    return f"{L} v {R}"

def smart_sentence(ctx: str) -> str:
    sents = re.split(r"(?<=[.!?])\s+", ctx or "")
    prefs = (" sued ", " files ", " filed ", " alleges ", " rules ", " granted ", " dismissed ", " injunction ", "certif", "settle", "summary judgment")
    for s in sents:
        ss = " " + s.lower() + " "
        if any(p in ss for p in prefs) and 44 <= len(s) <= 360:
            return s.strip()
    # fallback: first 2–3 sentences up to ~420 chars
    take, out = 0, []
    for s in sents:
        if not s.strip(): continue
        out.append(s.strip())
        take += 1
        if take >= 3 or len(" ".join(out)) >= 420:
            break
    if out:
        return " ".join(out)
    return (ctx or "")[:240].strip()

def infer_status_outcome(text: str):
    t = (text or "").lower()
    if "class certification" in t or "class certified" in t or "certify the class" in t:
        return ("Class certified", "Class Certification")
    if "summary judgment" in t or ("fair use" in t and "judgment" in t):
        return ("Judgment", "Summary Judgment")
    if "preliminary injunction" in t or "permanent injunction" in t or ("injunction" in t and "granted" in t):
        return ("Injunction", "Injunction")
    if "dismissed with prejudice" in t:
        return ("Dismissed", "Dismissal with prejudice")
    if "dismissed" in t or "motion to dismiss granted" in t:
        return ("Dismissed", "Dismissal")
    if "settlement" in t or "settled" in t or ("$" in t and "settle" in t):
        return ("Settled", "Settlement")
    if "complaint filed" in t or "new case" in t or "filed on" in t or "new case alert" in t:
        return ("Recently filed", "Update")
    return ("Open/Active", "Update")

def headline_for(caption: str, ctx: str) -> str:
    t = (ctx or "").lower()
    if "fair use" in t and ("judgment" in t or "summary judgment" in t):
        return f"{caption} - rules AI training fair use."
    if "class certification" in t or "class certified" in t:
        return f"{caption} - certifies class in AI/IP case."
    if "injunction" in t:
        return f"{caption} - injunction regarding AI use."
    if "dismiss" in t:
        return f"{caption} - dismisses AI/IP claims."
    if "settle" in t or "settlement" in t:
        m = re.search(r"\$\s?([0-9][\d\.,]+)\s*(billion|million|bn|m)?", ctx or "", re.I)
        if m:
            amt = m.group(0)
            return f"{caption} - settlement ({amt})."
        return f"{caption} - settlement."
    return caption

def choose_takeaway(status: str, ctx: str, already_has_takeaway: bool) -> str:
    if already_has_takeaway: return ""
    t = (ctx or "").lower(); s = (status or "").lower()
    if "settle" in t:
        if re.search(r"\$1\.?5\s*billion|\$1,?500,?000,?000", t):
            return "Historic settlement magnitude (~$1.5B) signals heavy liability where acquisition of works was unauthorized."
        return "Settlement values are emerging benchmarks; data acquisition practices can drive liability even if training is argued as fair use."
    if "judgment" in s and "fair use" in t and ("pirated" in t or "torrent" in t or "shadow library" in t):
        return "Even if training is fair use, acquisition via piracy/torrents can still create liability."
    if "judgment" in s and "fair use" in t and "market" in t:
        return "Courts weigh harm to the market for original works over a separate training license market."
    if "injunction" in s:
        return "Injunctions can constrain model distribution or retraining—leverage for ingestion guardrails."
    if "dismissed" in s:
        return "Claims that don’t connect copying to cognizable market harm risk dismissal."
    return ""

def prefer_source(a, b):
    pa = SOURCE_PREFERENCE.index(a["source"]) if a["source"] in SOURCE_PREFERENCE else 99
    pb = SOURCE_PREFERENCE.index(b["source"]) if b["source"] in SOURCE_PREFERENCE else 99
    if pa != pb: return pa < pb
    return len(a.get("summary","")) > len(b.get("summary",""))

def dedupe(items):
    norm = lambda s: re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()
    best = {}
    for it in items:
        k = norm(it["title"])
        if not k: continue
        if k not in best or prefer_source(it, best[k]):
            best[k] = it
    return list(best.values())

# -----------------------
# McKool Smith: always fetch latest edition and parse numbered items
# -----------------------

MCKOOL_INDEX = "https://www.mckoolsmith.com/newsroom-ailitigation"
MCKOOL_BASE  = "https://www.mckoolsmith.com/"

def mckool_find_latest_url(index_html: str) -> str:
    """
    Find the latest 'newsroom-ailitigation-<N>' by choosing the MAX N on the index.
    Normalize with urljoin to fix relative URLs like 'newsroom-ailitigation-35'.
    """
    soup = BeautifulSoup(index_html, "html.parser")
    best_href, best_n = None, -1
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        # normalize to absolute first so regex is consistent
        abs_href = urljoin(MCKOOL_BASE, href)
        m = re.search(r"/newsroom-ailitigation-(\d+)(?:/)?$", abs_href)
        if not m:
            continue
        n = int(m.group(1))
        if n > best_n:
            best_n = n
            best_href = abs_href
    if best_href:
        return best_href

    # Fallback: any ailitigation link (normalize)
    for a in soup.find_all("a", href=True):
        if "newsroom-ailitigation" in a["href"]:
            return urljoin(MCKOOL_BASE, a["href"].strip())

    return MCKOOL_INDEX

def mckool_parse_latest() -> list:
    idx = fetch(MCKOOL_INDEX)
    latest_url = mckool_find_latest_url(idx)
    print(f"[McKool] latest URL picked: {latest_url}", flush=True)
    article_html = fetch(latest_url)

    soup = BeautifulSoup(article_html, "html.parser")
    main = soup.find("main") or soup.find("article") or soup

    def text_of(node):
        return html.unescape(node.get_text("\n", strip=True))

    # Prefer numbered headings (e.g., "1. Bartz v. Anthropic")
    sections = []
    headings = main.find_all(re.compile(r"^h[1-6]$"))
    for h in headings:
        title = text_of(h)
        m = re.match(r"^\s*(\d+)\.\s*(.+)$", title)
        if not m:
            continue
        num = m.group(1)
        caption_line = m.group(2).strip()
        block_parts = []
        sib = h.next_sibling
        while sib:
            if getattr(sib, "name", None) and re.match(r"^h[1-6]$", sib.name):
                t2 = text_of(sib)
                if re.match(r"^\s*\d+\.\s+", t2):
                    break
            if hasattr(sib, "get_text"):
                block_parts.append(text_of(sib))
            sib = getattr(sib, "next_sibling", None)
        block_text = "\n".join([p for p in block_parts if p]).strip()
        sections.append((num, caption_line, block_text))

    # Fallback: line-based split on "N. "
    if not sections:
        text = text_of(main)
        raw_blocks = re.split(r"(?m)^\s*(\d+)\.\s+", text)
        for i in range(1, len(raw_blocks), 2):
            num = raw_blocks[i]
            body = raw_blocks[i+1]
            if not body:
                continue
            lines = body.split("\n")
            caption_line = lines[0].strip()
            block_text = "\n".join(lines[1:]).strip()
            sections.append((num, caption_line, block_text))

    print(f"[McKool] section count: {len(sections)}  url={latest_url}", flush=True)

    items = []
    for (num, caption_line, block_text) in sections:
        raw_caption = caption_line
        raw_caption = re.sub(r"\s+v\.\s+", " v ", raw_caption)
        raw_caption = re.sub(r"\s+v\s+", " v ", raw_caption)

        mcap = CAPTION_PAT.search(raw_caption)
        caption = compress_caption(mcap.group(0)) if mcap else raw_caption.strip()
        caption = re.sub(r"\s*\bbackground\b\s*$", "", caption, flags=re.I)

        # Extract labeled segments
        status_match = re.search(r"(?is)\bCurrent Status:\s*(.+?)(?:\n[A-Z][A-Za-z ]{2,20}:\s*|\Z)", block_text)
        background_match = re.search(r"(?is)\bBackground:\s*(.+?)(?:\n[A-Z][A-Za-z ]{2,20}:\s*|\Z)", block_text)
        status_text = (status_match.group(1).strip() if status_match else "")
        background_text = (background_match.group(1).strip() if background_match else "")

        # Lead sentence(s)
        lead = status_text or background_text or smart_sentence(block_text)

        # Infer status/outcome/headline/takeaway
        status, outcome = infer_status_outcome(status_text + " " + background_text)
        headline = headline_for(caption, status_text + " " + background_text)
        takeaway_guess = choose_takeaway(status, status_text + " " + background_text, already_has_takeaway=False)

        summary_html = f"<b>Summaries:</b> <b>{html.escape(caption)}</b> — {html.escape(lead)}"
        if takeaway_guess:
            summary_html += "<br><br><b>Key takeaway:</b> " + html.escape(takeaway_guess)

        frag = f"#sec-{num}"
        items.append({
            "title": caption,
            "headline": headline,
            "summary": summary_html,
            "takeaway": "",
            "status": status,
            "outcome": outcome,
            "source": "McKool Smith",
            "url": latest_url + frag,
            "case_ref": "",
            "date": ""
        })

    print(f"[McKool] built {len(items)} items", flush=True)
    # Do NOT filter McKool items by AI context — the page is curated.
    return items

# -----------------------
# Other sources — sectionize then mine captions
# -----------------------

def soup_sections(html_text: str, base_url: str):
    soup = BeautifulSoup(html_text, "html.parser")
    for bad in soup(["script","style","noscript","svg","nav","header","footer","form","aside"]):
        bad.decompose()
    container = soup.find("main") or soup.find("article") or soup.find("body") or soup
    headers = container.find_all(re.compile(r"^h[1-4]$"))
    if not headers:
        txt = container.get_text(" ", strip=True)
        return [("Page", re.sub(r"\s+", " ", html.unescape(txt)), base_url)]
    sections = []
    for i, h in enumerate(headers):
        title = h.get_text(" ", strip=True)
        frag = ""
        if h.has_attr("id"):
            frag = "#" + h["id"].strip()
        level = int(h.name[1])
        buff = []
        sib = h.next_sibling
        while sib:
            if getattr(sib, "name", None) and re.match(r"^h[1-4]$", sib.name):
                nxt_level = int(sib.name[1])
                if nxt_level <= level:
                    break
            if hasattr(sib, "get_text"):
                buff.append(sib.get_text(" ", strip=True))
            sib = getattr(sib, "next_sibling", None)
        raw = " ".join(buff)
        raw = html.unescape(raw)
        raw = re.sub(r"\s+", " ", raw).strip()
        sections.append((title, raw, base_url + frag))
    return sections

def extract_from_html(src_name: str, url: str):
    html_text = fetch(url)
    sections = soup_sections(html_text, url)
    items = []
    for (title, block, sec_url) in sections:
        if looks_like_junk(title, block):
            continue
        # find ALL captions in this section (title + early body)
        captions = set()
        for m in CAPTION_PAT.finditer(title + " " + block[:1500]):
            captions.add(m.group(0).strip())
        if not captions:
            continue
        for raw_caption in captions:
            caption = compress_caption(raw_caption)
            # For non-McKool sources, require AI/IP context or known parties
            has_ai_ctx = bool(AI_CONTEXT_PAT.search(block)) or any(
                p.lower() in (title + " " + block).lower() for p in AI_IP_PARTIES
            )
            if not has_ai_ctx:
                continue

            case_ref = ""
            cr = CASE_NO_PAT.search(block)
            if cr:
                case_ref = cr.group(0).strip()

            status, outcome = infer_status_outcome(block)
            headline = headline_for(caption, block)
            sent = smart_sentence(block)
            summary_html = f"<b>Summaries:</b> <b>{html.escape(caption)}</b> — {html.escape(sent)}"
            takeaway = choose_takeaway(status, block, already_has_takeaway=False)

            items.append({
                "title": caption,
                "headline": headline,
                "summary": summary_html,
                "takeaway": "" if not takeaway else takeaway,
                "status": status,
                "outcome": outcome,
                "source": src_name,
                "url": sec_url or url,
                "case_ref": case_ref,
                "date": ""
            })
    return items

# -----------------------
# Seeds (only used if something truly missing)
# -----------------------

SEED_CASES = [
    {
        "title": "Bartz v Anthropic",
        "headline": "Bartz v Anthropic - settlement (~$1.5B) and prior fair-use/train vs piracy distinction.",
        "summary": (
            "<b>Summaries:</b> <b>Bartz v Anthropic</b> — Proposed global settlement reported around $1.5B "
            "covering ~500,000 works (~$3,000/work), following Judge Alsup’s earlier ruling distinguishing "
            "training (potentially fair use) from mass pirated acquisition (not fair use).<br><br>"
            "<b>Key takeaway:</b> Even if training is fair use, acquisition via piracy/torrents can still create liability."
        ),
        "status": "Settled",
        "outcome": "Settlement",
    },
    {
        "title": "Kadrey et al. v Meta",
        "headline": "Kadrey et al. v Meta - N.D. Cal rules AI training fair use (on record presented).",
        "summary": (
            "<b>Summaries:</b> <b>Kadrey et al. v Meta</b> — Summary judgment found training on books to be fair use on the case record, "
            "but noted market harm pleadings could matter in other circumstances; ongoing disputes include issues around acquisition/torrenting context.<br><br>"
            "<b>Key takeaway:</b> Future pleadings that prove harm to the market for original works may fare better than focusing on a separate training-license market."
        ),
        "status": "Judgment",
        "outcome": "Summary Judgment",
    },
]

def ensure_seed_cases(items):
    have = {re.sub(r"[^a-z0-9]+"," ", it["title"].lower()).strip() for it in items}
    added = 0
    for s in SEED_CASES:
        norm = re.sub(r"[^a-z0-9]+"," ", s["title"].lower()).strip()
        if norm in have:
            continue
        items.append({
            "title": s["title"],
            "headline": s["headline"],
            "summary": s["summary"],
            "takeaway": "",
            "status": s["status"],
            "outcome": s["outcome"],
            "source": "Seeded",
            "url": "",
            "case_ref": "",
            "date": ""
        })
        added += 1
    return added

# -----------------------
# Output + runner
# -----------------------

def ensure_docs():
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(os.path.join(DOCS_DIR, ".nojekyll"), "w", encoding="utf-8") as f:
        f.write("")
    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(INDEX_HTML)

def run():
    print("[tracker] pulling latest McKool edition + other trackers", flush=True)
    all_items = []

    # 1) McKool Smith — authoritative weekly digest
    try:
        mk = mckool_parse_latest()
        print(f"[McKool] extracted: {len(mk)} items (first: {mk[0]['title'] if mk else '—'})", flush=True)
        all_items.extend(mk)
    except Exception as e:
        print(f"[McKool] ERROR: {e}", flush=True)

    # 2) Other trackers (best-effort, de-dup via caption)
    for src in SOURCES:
        if src["name"] == "McKool Smith":
            continue
        print(f"[scrape] {src['name']} -> {src['url']}", flush=True)
        try:
            items = extract_from_html(src["name"], src["url"])
        except Exception as e:
            print(f"[scrape] ERROR {src['name']}: {e}", flush=True)
            items = []
        print(f"[scrape]   extracted: {len(items)}", flush=True)
        all_items.extend(items)

    items = dedupe(all_items)
    seeded = ensure_seed_cases(items)
    if seeded:
        print(f"[seed] added {seeded} case(s) to cover gaps", flush=True)

    items.sort(key=lambda x: x["title"].lower())

    ensure_docs()
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    print(f"[tracker] wrote {JSON_PATH} with {len(items)} items", flush=True)

if __name__ == "__main__":
    run()

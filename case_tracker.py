#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Court Cases Tracker — parse four public trackers (no CourtListener)
Sources:
  - McKool Smith (cross-referenced to include ALL case captions found on their tracker page)
  - BakerHostetler
  - WIRED
  - Mishcon de Reya

Hard rules:
  - Only real captions "X v Y" (skip headings like "Case Updates", "Background", directories, ABC lists).
  - Require AI/IP context near the caption or a known AI litigant name.
  - Compress huge party lists to "Lead et al. v Lead et al."
  - Bold <b>Summaries:</b> + <b>Key takeaway:</b> (HTML, not Markdown).
  - Exact narratives for Bartz and Kadrey (match user's template).
  - De-duplicate across sources and prefer cleaner sources/longer summaries.
"""

import os, re, time, json, html
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

HEADERS = {"User-Agent": "AI-Cases-Tracker/7.0 (+GitHub Actions)"}

SOURCE_PREFERENCE = ["Mishcon de Reya LLP", "BakerHostetler", "McKool Smith", "WIRED"]

AI_IP_PARTIES = [
    "OpenAI","Anthropic","Meta","Google","Alphabet","Midjourney","Stability AI","Suno","Udio",
    "Perplexity","Cohere","Nvidia","Ross Intelligence","Thomson Reuters","Getty","Disney",
    "Universal","UMG","Warner","Sony","Authors Guild","New York Times","Reddit","Databricks",
    "LAION","GitHub","Microsoft","Bloomberg","IGN","Ziff Davis","Everyday Health","Dow Jones",
    "NY Post","Center for Investigative Reporting","Canadian Broadcasting Corporation","Radio-Canada"
]

CAPTION_PAT = re.compile(r"\b([A-Z][A-Za-z0-9\.\-’'& ]{1,90})\s+v\.?\s+([A-Z][A-Za-z0-9\.\-’'& ]{1,90})\b")
CASE_NO_PAT = re.compile(r"\b(\d{1,2}:\d{2}-cv-\d{4,6}[A-Za-z\-]*|No\.\s?[A-Za-z0-9\-\.:]+|Case\s?(?:No\.|#)\s?[A-Za-z0-9\-:]+|Claim\s?No\.\s?[A-Za-z0-9\-]+)\b", re.I)
AI_CONTEXT_PAT = re.compile(
    r"\b(ai|artificial intelligence|gen(?:erative)? ai|llm|model|training|dataset|copyright|dmca|"
    r"right of publicity|digital replica|scrap(?:e|ing)|music|recordings?|publisher|labels?)\b",
    re.I,
)
JUNK_HEADINGS_PAT = re.compile(
    r"^(case updates|current edition|our professionals|disclaimer|training & development|"
    r"a b c d e f g h i j k l m n o p q r s t u v w x y z|interactive entertainment|"
    r"blockchain, crypto and digital assets|mishcon purpose|philanthropic strategy|supply chain advice)\b",
    re.I
)

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
  <div class="sub">De-duplicated case summaries across McKool Smith, BakerHostetler, WIRED, and Mishcon. Rulings include a bold <b>Key takeaway</b>.</div>

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
# HTTP fetch and parsing
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

def soup_sections(html_text: str, base_url: str):
    """
    Yield structured sections: (title_text, section_text, section_url)
    Section = header (h1-h4) + following siblings until the next header of same or greater level.
    """
    soup = BeautifulSoup(html_text, "html.parser")

    for bad in soup(["script","style","noscript","svg","nav","header","footer","form","aside"]):
        bad.decompose()

    container = soup.find("main") or soup.find("article") or soup.find("body") or soup

    headers = container.find_all(re.compile(r"^h[1-4]$"))
    if not headers:
        # Fallback: treat the whole page as one section
        txt = container.get_text(" ", strip=True)
        return [("Page", re.sub(r"\s+", " ", html.unescape(txt)), base_url)]

    sections = []
    for i, h in enumerate(headers):
        title = h.get_text(" ", strip=True)
        # Build anchor or fragment if present
        frag = ""
        if h.has_attr("id"):
            frag = "#" + h["id"].strip()
        else:
            aid = h.find("a", attrs={"id": True})
            aname = h.find("a", attrs={"name": True})
            if aid and aid.get("id"):
                frag = "#" + aid["id"].strip()
            elif aname and aname.get("name"):
                frag = "#" + aname["name"].strip()

        # Collect siblings until the next header of same or higher level
        level = int(h.name[1])
        buff = []
        sib = h.next_sibling
        while sib:
            # Stop if next header of same or higher level
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

# ---------------
# Heuristics
# ---------------

def looks_like_junk(title: str, ctx: str) -> bool:
    if JUNK_HEADINGS_PAT.match(title.strip()):
        return True
    low = (title + " " + ctx).lower()
    if "our professionals" in low:
        return True
    if re.search(r"\bA B C D E F G\b", title):
        return True
    if "background" in title.lower():
        return True
    if "case updates" in title.lower():
        return True
    return False

def compress_caption(caption: str) -> str:
    cap = re.sub(r"\(\d+\)\s*", "", caption)
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
    return f"{L} v {R}"

def smart_sentence(ctx: str) -> str:
    sents = re.split(r"(?<=[.!?])\s+", ctx)
    prefs = (" sued ", " files ", " filed ", " alleges ", " rules ", " granted ", " dismissed ", " injunction ", "certif")
    for s in sents:
        ss = " " + s.lower() + " "
        if any(p in ss for p in prefs) and 44 <= len(s) <= 360:
            return s.strip()
    for s in sents:
        if 44 <= len(s) <= 360:
            return s.strip()
    return sents[0].strip() if sents else ctx[:240].strip()

def infer_status_outcome(text: str):
    t = text.lower()
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
    if "settled" in t or "settlement" in t:
        return ("Settled", "Settlement")
    if "complaint filed" in t or "new case" in t or "filed on" in t:
        return ("Recently filed", "Update")
    return ("Open/Active", "Update")

def headline_for(caption: str, ctx: str) -> str:
    t = ctx.lower()
    if "fair use" in t and ("judgment" in t or "summary judgment" in t):
        return f"{caption} - rules AI training fair use."
    if "class certification" in t or "class certified" in t:
        return f"{caption} - certifies class in AI/IP case."
    if "injunction" in t:
        return f"{caption} - injunction regarding AI use."
    if "dismiss" in t:
        return f"{caption} - dismisses AI/IP claims."
    if "settle" in t:
        return f"{caption} - settlement."
    return caption

def explicit_template_or_short(caption: str, ctx: str) -> str:
    # Hard-coded, HTML-renderable templates for the two “must-have” decisions
    if re.search(r"\bBartz\b", caption) and "Anthropic" in caption:
        return (
            "<b>Summaries:</b> "
            f"<b>{caption} - N.D. Cal rules AI training fair use.</b> "
            "On June 23, Judge Alsup granted partial summary judgment to Anthropic, ruling that "
            "1) training Claude on plaintiffs’ books was “exceedingly transformative” and fair use, and "
            "2) digitizing purchased physical books for Anthropic’s central library was also fair use. "
            "But the court declined to extend the fair use ruling to Anthropic downloading over 7 million pirated books for its central library, "
            "saying that such piracy is “inherently, irredeemably infringing” regardless of whether the copies are later put to a fair use. "
            "This issue will proceed to trial.<br><br>"
            "<b>Key takeaway:</b> Even where training is fair use, developers may still face significant liability for downloading pirated content."
        )

    if re.search(r"\bKadrey\b", caption) and "Meta" in caption:
        return (
            "<b>Summaries:</b> "
            f"<b>{caption} - N.D. Cal rules AI training fair use.</b> "
            "On June 25, Judge Chhabria granted summary judgment on fair use for AI training to Meta finding that "
            "1) using plaintiffs’ books to train Meta’s LLM was highly transformative, and "
            "2) plaintiffs are not entitled to the market for licensing for AI training. "
            "But Judge Chhabria noted that his decision was limited to the specific record of the case, and that in many circumstances training would not be fair use. "
            "He indicated that the order likely would have been different if plaintiffs had pled harm to the market for their original works, and even criticized Judge Alsup’s Bartz order for brushing aside market harm concerns.<br><br>"
            "<b>Key takeaway:</b> Future pleadings may be more successful if they focus on harm to the market for the original works and not only on the harm to the market for training licenses."
        )

    sent = smart_sentence(ctx).strip()
    if not sent:
        sent = "Summary not available from the source excerpt; details pending."

    # Escape plain text sentence; we deliberately keep <b> tags unescaped.
    return f"<b>Summaries:</b> {html.escape(sent)}"

def choose_takeaway(status: str, ctx: str, already_has_takeaway: bool) -> str:
    if already_has_takeaway: return ""
    t = ctx.lower(); s = status.lower()
    if "judgment" in s and "fair use" in t and ("pirated" in t or "shadow library" in t or "torrent" in t):
        return "Even if training is fair use, acquisition of pirated datasets can still create liability."
    if "judgment" in s and "fair use" in t and "market" in t:
        return "Courts weigh harm to the market for original works more than a separate training license market."
    if "injunction" in s:
        return "Injunctions can constrain model distribution or retraining, creating leverage for ingestion guardrails."
    if "dismissed" in s:
        return "Complaints that do not connect copying to cognizable market harm risk dismissal."
    if "settled" in s:
        return "Settlements set practical value ranges even without merits rulings."
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

# ---------------
# Section-based extraction (generic)
# ---------------

def extract_from_html(src_name: str, url: str):
    html_text = fetch(url)
    sections = soup_sections(html_text, url)

    items = []
    for (title, block, sec_url) in sections:
        if not title and not block:
            continue

        if looks_like_junk(title, block):
            continue

        # Find ALL captions within this section (some sections list multiple cases)
        captions = set()
        for m in CAPTION_PAT.finditer(title + " " + block[:1000]):
            captions.add(m.group(0).strip())

        if not captions:
            continue

        for raw_caption in captions:
            caption = compress_caption(raw_caption)

            has_ai_ctx = bool(AI_CONTEXT_PAT.search(block)) or any(
                p.lower() in (title + " " + block).lower() for p in AI_IP_PARTIES
            )
            if not has_ai_ctx:
                continue

            # Case reference
            case_ref = ""
            cr = CASE_NO_PAT.search(block)
            if cr:
                case_ref = cr.group(0).strip()

            status, outcome = infer_status_outcome(block)
            headline = headline_for(caption, block)
            summary_html = explicit_template_or_short(caption, block).strip()

            takeaway = ""
            if "<b>Key takeaway:</b>" not in summary_html:
                t_guess = choose_takeaway(status, block, already_has_takeaway=False)
                takeaway = t_guess

            if summary_html.lower() in ("<b>summaries:</b>", "<b>summaries:</b> "):
                summary_html = "<b>Summaries:</b> Summary not available from the source excerpt; details pending."

            items.append({
                "title": caption,
                "headline": headline,
                "summary": summary_html,   # HTML
                "takeaway": takeaway,
                "status": status,
                "outcome": outcome,
                "source": src_name,
                "url": sec_url or url,
                "case_ref": case_ref,
                "date": ""
            })
    return items

# ---------------
# McKool cross-reference to ensure ALL cases present
# ---------------

def cross_reference_mckool(items):
    """
    Fetch the McKool Smith tracker and ensure every 'X v Y' caption on that page
    appears in the final list at least once with a local summary from its section.
    """
    try:
        url = next((s["url"] for s in SOURCES if s["name"] == "McKool Smith"), None)
        if not url:
            return 0

        html_text = fetch(url)
        sections = soup_sections(html_text, url)

        # Build a map: caption -> best candidate item constructed from its section
        candidates = {}
        for (title, block, sec_url) in sections:
            if looks_like_junk(title, block):
                continue

            # Gather all captions in this section
            for m in CAPTION_PAT.finditer(title + " " + block[:2000]):
                raw_caption = m.group(0).strip()
                caption = compress_caption(raw_caption)

                # Light context gate: keep only plausible AI/IP mentions
                if not (AI_CONTEXT_PAT.search(block) or any(p.lower() in (title + " " + block).lower() for p in AI_IP_PARTIES)):
                    continue

                status, outcome = infer_status_outcome(block)
                headline = headline_for(caption, block)
                summary_html = explicit_template_or_short(caption, block).strip()
                takeaway = ""
                if "<b>Key takeaway:</b>" not in summary_html:
                    takeaway = choose_takeaway(status, block, already_has_takeaway=False)

                case_ref = ""
                cr = CASE_NO_PAT.search(block)
                if cr:
                    case_ref = cr.group(0).strip()

                cand = {
                    "title": caption,
                    "headline": headline,
                    "summary": summary_html,
                    "takeaway": takeaway,
                    "status": status,
                    "outcome": outcome,
                    "source": "McKool Smith",
                    "url": sec_url or url,
                    "case_ref": case_ref,
                    "date": ""
                }
                # Prefer longer summaries
                key = re.sub(r"[^a-z0-9]+"," ", caption.lower()).strip()
                if key not in candidates or len(summary_html) > len(candidates[key]["summary"]):
                    candidates[key] = cand

        # Merge any missing captions into items
        have = {re.sub(r"[^a-z0-9]+"," ", it["title"].lower()).strip() for it in items}
        added = 0
        for key, cand in candidates.items():
            if key not in have:
                items.append(cand)
                added += 1
        return added
    except Exception as e:
        print(f"[xref] ERROR cross-referencing McKool Smith: {e}", flush=True)
        return 0

# ---------------
# Must-have seeds (Bartz + Kadrey) in exact template
# ---------------

SEED_CASES = [
    {
        "needle": re.compile(r"\bBartz\b.*\bAnthropic\b", re.I),
        "title": "Bartz v Anthropic",
        "headline": "Bartz v Anthropic - N.D. Cal rules AI training fair use.",
        "summary": (
            "<b>Summaries:</b> <b>Bartz v Anthropic - N.D. Cal rules AI training fair use.</b> "
            "On June 23, Judge Alsup granted partial summary judgment to Anthropic, ruling that "
            "1) training Claude on plaintiffs’ books was “exceedingly transformative” and fair use, and "
            "2) digitizing purchased physical books for Anthropic’s central library was also fair use. "
            "But the court declined to extend the fair use ruling to Anthropic downloading over 7 million pirated books for its central library, "
            "saying that such piracy is “inherently, irredeemably infringing” regardless of whether the copies are later put to a fair use. "
            "This issue will proceed to trial.<br><br>"
            "<b>Key takeaway:</b> Even where training is fair use, developers may still face significant liability for downloading pirated content."
        ),
        "status": "Judgment",
        "outcome": "Summary Judgment",
    },
    {
        "needle": re.compile(r"\bKadrey\b.*\bMeta\b", re.I),
        "title": "Kadrey et al. v Meta",
        "headline": "Kadrey et al. v Meta - N.D. Cal rules AI training fair use.",
        "summary": (
            "<b>Summaries:</b> <b>Kadrey et al. v Meta - N.D. Cal rules AI training fair use.</b> "
            "On June 25, Judge Chhabria granted summary judgment on fair use for AI training to Meta finding that "
            "1) using plaintiffs’ books to train Meta’s LLM was highly transformative, and "
            "2) plaintiffs are not entitled to the market for licensing for AI training. "
            "But Judge Chhabria noted that his decision was limited to the specific record of the case, and that in many circumstances training would not be fair use. "
            "He indicated that the order likely would have been different if plaintiffs had pled harm to the market for their original works, and even criticized Judge Alsup’s Bartz order for brushing aside market harm concerns.<br><br>"
            "<b>Key takeaway:</b> Future pleadings may be more successful if they focus on harm to the market for the original works and not only on the harm to the market for training licenses."
        ),
        "status": "Judgment",
        "outcome": "Summary Judgment",
    },
]

def ensure_seed_cases(items):
    titles = {re.sub(r"[^a-z0-9]+"," ", it["title"].lower()).strip() for it in items}
    added = 0
    for seed in SEED_CASES:
        norm = re.sub(r"[^a-z0-9]+"," ", seed["title"].lower()).strip()
        if norm in titles:
            continue
        if any(seed["needle"].search(it["title"]) for it in items):
            continue
        items.append({
            "title": seed["title"],
            "headline": seed["headline"],
            "summary": seed["summary"],
            "takeaway": "",  # already embedded in summary
            "status": seed["status"],
            "outcome": seed["outcome"],
            "source": "Seeded",
            "url": "",
            "case_ref": "",
            "date": ""
        })
        added += 1
    return added

# -----------------------
# Output and runner
# -----------------------

def ensure_docs():
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(os.path.join(DOCS_DIR, ".nojekyll"), "w", encoding="utf-8") as f:
        f.write("")
    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(INDEX_HTML)

def run():
    print("[tracker] scraping four public trackers (no CourtListener)", flush=True)
    all_items = []
    for src in SOURCES:
        print(f"[scrape] {src['name']} -> {src['url']}", flush=True)
        try:
            items = extract_from_html(src["name"], src["url"])
        except Exception as e:
            print(f"[scrape] ERROR {src['name']}: {e}", flush=True)
            items = []
        print(f"[scrape]   extracted: {len(items)}", flush=True)
        all_items.extend(items)

    # Cross-reference McKool Smith: ensure ALL case captions present from their tracker page
    print("[xref] ensuring all McKool Smith cases are included", flush=True)
    added_xref = cross_reference_mckool(all_items)
    print(f"[xref] added from McKool page: {added_xref}", flush=True)

    # De-duplicate and seed must-have decisions
    items = dedupe(all_items)
    seeded = ensure_seed_cases(items)
    if seeded:
        print(f"[seed] added {seeded} must-have case(s)", flush=True)

    # Sort and write
    items.sort(key=lambda x: x["title"].lower())
    ensure_docs()
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    print(f"[tracker] wrote {JSON_PATH} with {len(items)} items", flush=True)

if __name__ == "__main__":
    run()

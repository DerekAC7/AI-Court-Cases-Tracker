#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Key Generative AI Infringement Cases in Media and Entertainment — McKool-only edition
- Pulls the latest McKool weekly edition (newsroom-ailitigation-XX)
- Extracts the edition date printed under 'Current Edition...' (e.g., 09.07.2025)
- Builds a de-duped, clean list of cases with:
    • Headline
    • Bold <b>Summaries:</b> + optional <b>Key takeaway:</b>
    • Music-publisher expert lens (deterministic, tailored)
    • Link labeled “Source →” to a CourtListener search for the caption
- Outputs to GitHub Pages:
    • docs/index.html
    • docs/cases.json

Requires:
  pip install requests beautifulsoup4
"""

import os
import re
import time
import json
import html
import urllib.parse
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# ==============================
# Config & Constants
# ==============================

DOCS_DIR = "docs"
INDEX_PATH = os.path.join(DOCS_DIR, "index.html")
JSON_PATH = os.path.join(DOCS_DIR, "cases.json")

HEADERS = {"User-Agent": "AI-Cases-Tracker/16.1 (+GitHub Pages/Actions)"}

MCKOOL_INDEX = "https://www.mckoolsmith.com/newsroom-ailitigation"
MCKOOL_BASE  = "https://www.mckoolsmith.com/"

# Caption pattern like "X v Y" (or "X v. Y")
CAPTION_PAT = re.compile(
    r"\b([A-Z][A-Za-z0-9\.\-’'& ]{1,90})\s+v\.?\s+([A-Z][A-Za-z0-9\.\-’'& ]{1,90})\b",
    re.I
)

# Month-name date, e.g., "September 7, 2025"
DATE_WORD_PAT = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|"
    r"Sep(?:t\.?|tember)|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},\s+20\d{2}\b",
    re.I
)

# Flexible numeric header date used on McKool (accepts . / bullet / hyphen variants)
# Matches things like: 09.07.2025, 9·7·2025, 09/07/2025, 09-07-2025, etc.
DELIMS = r"\.\u2024\u2219\u00B7\u2027\u30FB/\-\u2010\u2011\u2012\u2013\u2014"
DATE_NUM_FLEX_PAT = re.compile(rf"(\d{{1,2}})[{DELIMS}](\d{{1,2}})[{DELIMS}](20\d{{2}})")

# ==============================
# Helpers
# ==============================

def format_us_date(dt: datetime) -> str:
    """Cross-platform 'Month D, YYYY' (no leading zero)."""
    month = dt.strftime("%B")
    return f"{month} {dt.day}, {dt.year}"

def fetch(url: str) -> str:
    for i in range(4):
        r = requests.get(url, headers=HEADERS, timeout=45)
        if r.status_code == 200:
            return r.text
        if r.status_code in (429, 503):
            time.sleep(2 + 2*i)
            continue
        r.raise_for_status()
    raise RuntimeError(f"Failed to fetch {url}")

def _clean_party_label(p: str, default_label: str) -> str:
    """Normalize a single party label and avoid lone 'et al.' results."""
    s = (p or "").strip()
    s = re.sub(r"^[,;]+|[,;]+$", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    if re.fullmatch(r"(?i)et\.?\s*al\.?", s):
        return default_label
    # Collapse repeated 'et al.'
    s = re.sub(r"(?i)\bet\.?\s*al\.?(?:\s*et\.?\s*al\.?)+", "et al.", s)
    return s

def compress_caption(caption: str) -> str:
    """
    Normalize captions into 'Lead et al. v Lead et al.' and strip trailing 'Background'.
    Prevents outputs like 'et al et al v Perplexity'.
    """
    cap = re.sub(r"\(\d+\)\s*", "", caption or "")
    m = re.search(r"\s+v\.?\s+", cap, flags=re.I)
    if not m:
        return cap.strip()

    left_raw, right_raw = cap[:m.start()], cap[m.end():]

    def first_party(side: str, default_label: str) -> str:
        parts = re.split(r"\s*,\s*| & | and |;|\s{2,}", side)
        lead = parts[0].strip() if parts and parts[0] else side.strip()
        lead = _clean_party_label(lead, default_label)
        if not lead:
            lead = default_label
        return lead

    def many(side: str) -> bool:
        return bool(re.search(r"(?i)\bet\.?\s*al\.?|,|\band\b|&", side)) or len(side) > 60

    L = first_party(left_raw, "Plaintiffs")
    R = first_party(right_raw, "Defendants")

    if many(left_raw) and not re.search(r"(?i)\bet\.?\s*al\.?$", L):
        L = f"{L} et al."
    if many(right_raw) and not re.search(r"(?i)\bet\.?\s*al\.?$", R):
        R = f"{R} et al."

    # Remove doubled 'et al.' and stray 'background'
    L = re.sub(r"(?i)\bet\.?\s*al\.?\s*et\.?\s*al\.?$", "et al.", L)
    R = re.sub(r"(?i)\bet\.?\s*al\.?\s*et\.?\s*al\.?$", "et al.", R)
    L = re.sub(r"(?i)\s*\bbackground\b\s*$", "", L)
    R = re.sub(r"(?i)\s*\bbackground\b\s*$", "", R)

    return f"{L} v {R}"

def smart_sentence(ctx: str) -> str:
    sents = re.split(r"(?<=[.!?])\s+", ctx or "")
    prefs = (" sued ", " files ", " filed ", " alleges ", " rules ", " granted ", " dismissed ",
             " injunction ", "certif", "settle", "summary judgment", "motion")
    for s in sents:
        ss = " " + s.lower() + " "
        if any(p in ss for p in prefs) and 44 <= len(s) <= 360:
            return s.strip()
    out, take = [], 0
    for s in sents:
        if not s.strip():
            continue
        out.append(s.strip())
        take += 1
        if take >= 3 or len(" ".join(out)) >= 420:
            break
    return " ".join(out) if out else (ctx or "")[:240].strip()

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
    if re.search(r"\b(granted|granting)\b.*\bmotion to dismiss\b", t):
        return ("Dismissed", "Dismissal")
    if "dismissed" in t:
        return ("Dismissed", "Dismissal")
    if "settlement" in t or "settled" in t or ("$" in t and "settle" in t):
        return ("Settled", "Settlement")
    if "complaint filed" in t or "new case" in t or "filed on" in t or "new case alert" in t:
        return ("Recently filed", "Update")
    return ("Open/Active", "Update")

def headline_for(caption: str, ctx: str) -> str:
    t = (ctx or "").lower()
    if re.search(r"\b(granted|granting)\b.*\bsummary judgment\b|\bsummary judgment (granted|entered)\b", t) or ("fair use" in t and "judgment" in t):
        return f"{caption} - rules AI training fair use."
    if "class certification" in t or "class certified" in t:
        return f"{caption} - certifies class in AI/IP case."
    if "injunction" in t and ("granted" in t or "preliminary" in t or "permanent" in t):
        return f"{caption} - injunction regarding AI use."
    if "settle" in t or "settlement" in t:
        m = re.search(r"\$\s?([0-9][\d\.,]+)\s*(billion|million|bn|m)?", ctx or "", re.I)
        if m:
            amt = m.group(0)
            return f"{caption} - settlement ({amt})."
    if "motion to dismiss" in t and not ("granted" in t or "denied" in t):
        return f"{caption} - motion to dismiss briefing."
    if "dismissed" in t or "dismisses" in t:
        return f"{caption} - dismisses AI/IP claims."
    return caption

def music_publisher_lens(caption: str, status_text: str, background_text: str) -> str:
    """Publisher-focused, deterministic expert guidance."""
    cap = (caption or "").lower()
    txt = f"{status_text or ''} {background_text or ''}".lower()

    if "bartz" in cap and "anthropic" in cap:
        return ("Settlement magnitude (~$3k/work) is a valuation anchor. "
                "Push provenance audits and disclosure of acquisition sources; leverage Bartz’s acquisition-vs-training split to frame composition claims and negotiations.")

    if ("kadrey" in cap or "silverman" in cap) and "meta" in cap:
        return ("Fair-use win here hinged on record-specific market-harm showings. "
                "For compositions, build a damages narrative tied to substitution (lyrics, sheet music, sync) and document lost licensing opportunities to survive early motions.")

    if "warner bros" in cap and "midjourney" in cap:
        return ("Outputs mimicking protected characters strengthen arguments that AI can reproduce protected expression. "
                "For lyrics/compositions, pursue evidence that prompts yield lyric-like outputs and prepare injunctive relief asks tied to output filters.")

    if ("umg" in cap or "universal" in cap) and ("suno" in cap or "udio" in cap or "uncharted" in cap):
        return ("Label-led pleadings focus on sound recordings; monitor discovery for training-data disclosures. "
                "If lyrics or compositions appear in ingestion logs, be prepared to assert composition-specific claims and request preservation of training artifacts.")

    if "concord" in cap and "anthropic" in cap:
        return ("Music-lyrics ingestion dispute: demand cross-use of Bartz discovery on torrenting and any overlap with lyric corpora. "
                "Seek unredacted dataset inventories and negotiate protective orders that allow publisher-side experts to inspect samples.")

    if "reddit" in cap and "anthropic" in cap:
        return ("TOS/robots-based claims highlight enforceable access controls. "
                "Harden publisher lyric-site terms and robots.txt, and preserve access logs to support contract/DMCA 1201 theories against unlicensed scrapers.")

    if "new york times" in cap and ("openai" in cap or "microsoft" in cap):
        return ("Preservation obligations may unlock ingestion and output logs. "
                "Request parallel preservation and model-audit protocols in music cases to surface lyric/composition usage and quantify market harm.")

    if "perplexity" in cap or " rag " in txt or "r.a.g" in txt:
        return ("RAG systems pose ongoing reproduction risks. "
                "Assert claims on output reproduction of lyrics and require link-respecting behavior; consider negotiating paid access APIs for lyric metadata as an alternative.")

    if "thomson reuters" in cap and "ross" in cap:
        return ("Court’s emphasis on market impact is instructive: develop evidence that unlicensed training forecloses licensing for compositions/lyrics. "
                "Frame publisher markets distinctly from any proposed 'training-license' market.")

    if "andersen" in cap or "stability" in cap or "midjourney" in cap:
        return ("Visual-art rulings on inducement and output similarity can carry over: "
                "document AI outputs that recreate lyric structure/phrases to support composition claims and push for output filtering obligations.")

    if "mdl" in cap or "multi-district" in cap or "southern district of new york" in txt:
        return ("Coordinate with aligned plaintiffs; file amicus on market-harm factors relevant to compositions. "
                "Track scheduling to time publisher filings with key expert discovery milestones.")

    if "summary judgment" in txt and "fair use" in txt:
        return ("Anticipate fair-use defenses: center evidence on market substitution for compositions (lyrics/sheet music) rather than separate 'training-license' markets.")
    if "injunction" in txt:
        return ("Seek injunction terms that require dataset disclosure and output filters for lyrics; tie relief to retraining constraints if compositions were ingested.")
    if "settlement" in txt or "settled" in txt or "$" in txt:
        return ("Use settlement figures to benchmark per-work valuation; pursue early settlement conferences backed by provenance audits and catalog-specific damages models.")
    if "robots" in txt or "terms of service" in txt or "trespass" in txt:
        return ("Strengthen site TOS and robots.txt for lyrics; maintain detailed access logs to support contract and anti-circumvention claims.")

    return ("Build evidentiary files on lyric/composition market harm (lost sync, sheet music, lyric licensing) and compel disclosure of training datasets and ingestion logs.")

# ==============================
# McKool scraping & parsing
# ==============================

def mckool_find_latest_url(index_html: str) -> str:
    soup = BeautifulSoup(index_html, "html.parser")
    best_href, best_n = None, -1
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
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
    # Fallback to any ailitigation link if numbering not found
    for a in soup.find_all("a", href=True):
        if "newsroom-ailitigation" in a["href"]:
            return urljoin(MCKOOL_BASE, a["href"].strip())
    return MCKOOL_INDEX

def extract_as_of_date(article_soup: BeautifulSoup) -> str:
    """
    Extract the edition date printed under the 'Current Edition...' header.

    Preference:
      1) First numeric date in the preface (MM.DD.YYYY with flexible separators).
      2) Month-name date in the preface (e.g., September 7, 2025).
      3) Fallback: today's UTC date.

    The 'preface' is everything between the 'Current Edition...' header and the
    first numbered case heading ("1. ..."). We avoid scanning the entire page so
    we don't accidentally pick up dates embedded in case backgrounds.
    Always returns a string.
    """
    main = article_soup.find("main") or article_soup.find("article") or article_soup

    # Locate the "Current Edition" header element
    ce_text_node = main.find(string=re.compile(r"^\s*Current Edition", re.I))
    if ce_text_node and getattr(ce_text_node, "parent", None):
        start_el = ce_text_node.parent
    else:
        # Fallback: first heading in main
        start_el = None
        for h in main.find_all(re.compile(r"^h[1-6]$")):
            start_el = h
            break
        if not start_el:
            start_el = main

    # Walk forward sibling-by-sibling until the first numbered heading
    def is_numbered_heading(tag) -> bool:
        if not getattr(tag, "name", None):
            return False
        if not re.match(r"^h[1-6]$", tag.name):
            return False
        t = tag.get_text(" ", strip=True)
        return bool(re.match(r"^\s*\d+\.\s+", t))

    preface_chunks, node, hops = [], getattr(start_el, "next_sibling", None), 0
    while node and hops < 120:  # generous but bounded
        hops += 1
        if hasattr(node, "name") and is_numbered_heading(node):
            break
        if hasattr(node, "get_text"):
            preface_chunks.append(node.get_text(" ", strip=True))
        node = getattr(node, "next_sibling", None)

    preface_text = " ".join([p for p in preface_chunks if p]).strip()

    # 1) Prefer a numeric MM.DD.YYYY-style date with flexible separators in the preface
    m = DATE_NUM_FLEX_PAT.search(preface_text)
    if m:
        mm, dd, yyyy = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return format_us_date(datetime(yyyy, mm, dd))
        except ValueError:
            pass

    # 2) Month-name date in the preface
    m2 = DATE_WORD_PAT.search(preface_text)
    if m2:
        raw = m2.group(0)
        for fmt_try in ("%B %d, %Y", "%b %d, %Y"):
            try:
                dt = datetime.strptime(raw, fmt_try)
                return format_us_date(dt)
            except ValueError:
                continue

    # 3) Final fallback: today (UTC)
    return format_us_date(datetime.utcnow())

def mckool_parse_latest():
    idx = fetch(MCKOOL_INDEX)
    latest_url = mckool_find_latest_url(idx)
    print(f"[McKool] latest URL picked: {latest_url}", flush=True)
    article_html = fetch(latest_url)

    soup = BeautifulSoup(article_html, "html.parser")
    main = soup.find("main") or soup.find("article") or soup
    as_of_date = extract_as_of_date(soup) or format_us_date(datetime.utcnow())

    def text_of(node):
        return html.unescape(node.get_text("\n", strip=True))

    # Prefer numbered headings "1. Title"
    sections = []
    headings = main.find_all(re.compile(r"^h[1-6]$"))
    for h in headings:
        title = text_of(h)
        m = re.match(r"^\s*(\d+)\.\s*(.+)$", title)
        if not m:
            continue
        num = m.group(1)
        caption_line = m.group(2).strip()

        # Collect the block until the next numbered heading
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

    # Fallback: line split
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
    for (_num, caption_line, block_text) in sections:
        # Normalize caption to "Lead et al. v Lead et al."
        raw_caption = re.sub(r"\s+v\.\s+", " v ", caption_line, flags=re.I)
        raw_caption = re.sub(r"\s+v\s+", " v ", raw_caption, flags=re.I)
        mcap = CAPTION_PAT.search(raw_caption)
        caption = compress_caption(mcap.group(0)) if mcap else raw_caption.strip()
        caption = re.sub(r"(?i)\s*\bbackground\b\s*$", "", caption)

        # Pull labeled segments
        status_match = re.search(r"(?is)\bCurrent Status:\s*(.+?)(?:\n[A-Z][A-Za-z &]{2,30}:\s*|\Z)", block_text)
        background_match = re.search(r"(?is)\bBackground:\s*(.+?)(?:\n[A-Z][A-Za-z &]{2,30}:\s*|\Z)", block_text)
        status_text = (status_match.group(1).strip() if status_match else "")
        background_text = (background_match.group(1).strip() if background_match else "")

        lead = status_text or background_text or smart_sentence(block_text)
        status, outcome = infer_status_outcome(status_text + " " + background_text)
        headline = headline_for(caption, status_text + " " + background_text)

        # Optional generic key takeaway (short, neutral)
        generic_takeaway = ""
        t = (status_text + " " + background_text).lower()
        if "fair use" in t and ("judgment" in t or "summary judgment" in t):
            generic_takeaway = "Fair-use outcomes may hinge on record-specific market-harm proof."
        elif "settlement" in t or "settled" in t:
            generic_takeaway = "Settlement figures are emerging benchmarks for per-work valuation."
        elif "injunction" in t:
            generic_takeaway = "Injunctive relief can impose output filters and retraining constraints."

        pub_lens = music_publisher_lens(caption, status_text, background_text)

        # Bold "Summaries:" and caption
        summary_html = f"<b>Summaries:</b> <b>{html.escape(caption)}</b> — {html.escape(lead)}"
        if generic_takeaway:
            summary_html += "<br><br><b>Key takeaway:</b> " + html.escape(generic_takeaway)

        # Link to original filings via CourtListener search
        src_url = courtlistener_search_url(caption, hint_text=status_text + " " + background_text)

        items.append({
            "title": caption,
            "headline": headline,
            "summary": summary_html,
            "takeaway": "",
            "music_lens": pub_lens,
            "status": status,
            "outcome": outcome,
            "source": "",
            "url": src_url,
            "case_ref": "",
            "date": as_of_date
        })

    print(f"[McKool] built {len(items)} items; as_of={as_of_date}", flush=True)
    return items, as_of_date

# ==============================
# CourtListener search URL
# ==============================

def courtlistener_search_url(caption: str, hint_text: str = "") -> str:
    q = caption
    hint = ""
    ht = (hint_text or "").lower()
    if "n.d. cal" in ht or "northern district of california" in ht:
        hint = " AND (court:(california northern))"
    elif "s.d.n.y." in ht or "southern district of new york" in ht:
        hint = " AND (court:(new york southern))"
    elif "d. mass" in ht or "district of massachusetts" in ht:
        hint = " AND (court:(massachusetts))"
    elif "d. del" in ht or "district of delaware" in ht:
        hint = " AND (court:(delaware))"
    query = urllib.parse.quote_plus(q + hint)
    return f"https://www.courtlistener.com/?q={query}&type=r&order_by=score%20desc"

# ==============================
# HTML UI
# ==============================

def build_index_html(as_of: str) -> str:
    as_of = as_of or format_us_date(datetime.utcnow())
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Key Generative AI Infringement Cases in Media and Entertainment</title>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<style>
  :root {{ --fg:#0f172a; --muted:#475569; --bg:#ffffff; --card:#f8fafc; --line:#e2e8f0; --pill:#0ea5e9; --pill2:#7c3aed; }}
  *{{box-sizing:border-box}}
  html,body{{margin:0;padding:0}}
  body{{font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif; color:var(--fg); background:var(--bg); padding:24px;}}
  h1{{margin:0 0 4px 0; font-size:26px; font-weight:800}}
  .sub{{color:var(--muted); margin:0 0 16px 0; font-size:14px}}
  .toolbar{{display:flex; gap:12px; margin:12px 0 18px; flex-wrap:wrap}}
  input,select{{padding:10px 12px; border:1px solid var(--line); border-radius:10px; font-size:14px}}
  .grid{{display:grid; grid-template-columns:repeat(auto-fill, minmax(420px,1fr)); gap:14px}}
  .card{{background:var(--card); border:1px solid var(--line); border-radius:14px; padding:16px; display:flex; flex-direction:column; gap:10px}}
  .title{{font-weight:800; font-size:16px; line-height:1.35}}
  .meta{{font-size:12px; color:var(--muted); display:flex; gap:8px; align-items:center; flex-wrap:wrap}}
  .pill{{background:var(--pill); color:#fff; border-radius:999px; padding:3px 8px; font-size:11px; font-weight:700}}
  .pill2{{background:var(--pill2); color:#fff; border-radius:999px; padding:3px 8px; font-size:11px; font-weight:700}}
  .summary{{font-size:14px; line-height:1.5}}
  .summary b{{font-weight:800}}
  .footer{{display:flex; justify-content:space-between; align-items:center}}
  .linkbtn{{display:inline-block; padding:8px 10px; border-radius:10px; border:1px solid var(--line); background:#fff; font-size:13px; text-decoration:none}}
  .linkbtn:hover{{text-decoration:underline}}
  .ref{{font-size:12px; color:var(--muted)}}
</style>
</head>
<body>
  <h1>Key Generative AI Infringement Cases in Media and Entertainment</h1>
  <div class="sub">as of {html.escape(as_of)}</div>

  <div class="toolbar">
    <input id="q" type="search" placeholder="Filter by case, outcome, status…" aria-label="Filter"/>
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
async function load() {{
  const list = document.getElementById('list');
  try {{
    const res = await fetch('cases.json', {{cache:'no-store'}});
    if (!res.ok) throw new Error('Failed to load cases.json: ' + res.status);
    const data = await res.json();

    const q = document.getElementById('q');
    const sortSel = document.getElementById('sort');
    const statusSel = document.getElementById('status');

    function render(filter='', sortBy='title', statusFilter='') {{
      const f = filter.toLowerCase();
      let items = data.filter(c => {{
        const hay = (c.headline + ' ' + (c.outcome||'') + ' ' + (c.source||'') + ' ' + (c.summary||'') + ' ' + (c.status||'')).toLowerCase();
        const passText = !f || hay.includes(f);
        const passStatus = !statusFilter || (c.status||'').toLowerCase() === statusFilter.toLowerCase();
        return passText && passStatus;
      }});

      items.sort((a,b)=>{{
        const ax=(a[sortBy]||'').toString().toLowerCase();
        const bx=(b[sortBy]||'').toString().toLowerCase();
        return ax.localeCompare(bx);
      }});

      list.innerHTML = '';
      items.forEach(c=>{{
        const url = c.url || '#';
        const status  = c.status || 'Open/Active';
        const headline = (c.headline || c.title || 'Case');
        const cref = c.case_ref ? `<span class="ref">Case ref: ${'{'}c.case_ref{'}'}</span>` : '';
        const hasEmbeddedKT = (c.summary||"").toLowerCase().includes("<b>key takeaway:</b>");
        const ktInline = (!hasEmbeddedKT && c.takeaway)
          ? `<div class="summary"><b>Key takeaway:</b> ${'{'}c.takeaway{'}'}</div>`
          : '';

        const card = document.createElement('div');
        card.className = 'card';
        card.innerHTML = `
          <div class="title">${'{'}headline{'}'}</div>
          <div class="meta">
            <span class="pill">${'{'}status{'}'}</span>
            ${'{'}c.outcome && c.outcome !== 'Update' ? `<span class="pill2">${'{'}c.outcome{'}'}</span>` : ''{'}'}
            ${'{'}cref{'}'}
          </div>
          <div class="summary">${'{'}c.summary || '<b>Summaries:</b> No summary available.'{'}'}</div>
          ${'{'}ktInline{'}'}
          ${'{'}c.music_lens ? `<div class="summary"><b>Music lens:</b> ${'{'}c.music_lens{'}'}</div>` : ''{'}'}
          <div class="footer">
            <span></span>
            <a class="linkbtn" href="${'{'}url{'}'}" target="_blank" rel="noopener">Source →</a>
          </div>
        `;
        list.appendChild(card);
      }});

      if (items.length === 0) {{
        list.innerHTML = '<div class="card"><div class="title">No cases found</div><div class="summary">Try clearing filters or check back later.</div></div>';
      }}
    }}

    q.addEventListener('input', (e)=>render(e.target.value, sortSel.value, statusSel.value));
    sortSel.addEventListener('change', ()=>render(q.value, sortSel.value, statusSel.value));
    statusSel.addEventListener('change', ()=>render(q.value, sortSel.value, statusSel.value));
    render();
  }} catch (e) {{
    list.innerHTML = '<div class="card"><div class="title">Site is initializing</div><div class="summary">Could not load <code>cases.json</code>. Verify the Action wrote <code>docs/cases.json</code> and Pages is set to main /docs.</div></div>';
    console.error(e);
  }}
}}
load();
</script>
</body>
</html>"""

# ==============================
# Output & Runner
# ==============================

def ensure_docs(as_of: str):
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(os.path.join(DOCS_DIR, ".nojekyll"), "w", encoding="utf-8") as f:
        f.write("")
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        f.write(build_index_html(as_of))

def run():
    print("[tracker] pulling latest McKool edition (publisher-focused)", flush=True)
    try:
        items, as_of = mckool_parse_latest()
    except Exception as e:
        print(f"[McKool] ERROR: {e}", flush=True)
        items, as_of = [], format_us_date(datetime.utcnow())

    # Safety: ensure we always have a human-readable date string
    if not as_of:
        as_of = format_us_date(datetime.utcnow())

    # Sort & write
    items = sorted(items, key=lambda x: x["title"].lower())
    ensure_docs(as_of)
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    print(f"[tracker] wrote {JSON_PATH} with {len(items)} items; index subtitle 'as of {as_of}'", flush=True)

if __name__ == "__main__":
    run()

import os
import re
import hashlib
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse

# ========= CONFIG =========
REPO_OWNER = "DerekAC7"
REPO_NAME  = "AI-Court-Cases-Tracker"
TOKEN = os.getenv("PERSONAL_ACCESS_TOKEN")  # mapped from secrets.PAT_TOKEN in the workflow
COMMON_LABELS = ["AI Training", "Court Case"]
SOURCE_LABEL_PREFIX = "Source: "
TIMEOUT = 30
# =========================

API_BASE = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}"

def gh_headers():
    if not TOKEN:
        raise RuntimeError("Missing token. In the workflow, map secrets.PAT_TOKEN to PERSONAL_ACCESS_TOKEN.")
    return {
        "Authorization": f"token {TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "ai-litigation-tracker-bot"
    }

# ---------- GitHub helpers ----------
def ensure_labels(extra_labels):
    r = requests.get(f"{API_BASE}/labels?per_page=100", headers=gh_headers())
    r.raise_for_status()
    have = {x["name"] for x in r.json()}
    for name in set(COMMON_LABELS) | set(extra_labels):
        if name not in have:
            requests.post(f"{API_BASE}/labels", headers=gh_headers(), json={"name": name})

def list_existing_issue_keys():
    """
    We de-duplicate using a stable key derived from title+source.
    Store that key in the issue body as: <!-- KEY: ... -->
    """
    keys = set()
    for state in ("open", "closed"):
        page = 1
        while True:
            r = requests.get(f"{API_BASE}/issues", headers=gh_headers(),
                             params={"state": state, "per_page": 100, "page": page})
            r.raise_for_status()
            items = r.json()
            if not items:
                break
            for it in items:
                if "pull_request" in it:
                    continue
                m = re.search(r"<!--\s*KEY:\s*([a-f0-9]{32})\s*-->", it.get("body") or "", flags=re.I)
                if m:
                    keys.add(m.group(1))
            page += 1
    return keys

def create_issue(title, body, labels):
    payload = {"title": title, "body": body, "labels": labels}
    r = requests.post(f"{API_BASE}/issues", headers=gh_headers(), json=payload)
    if r.status_code != 201:
        raise RuntimeError(f"Issue create failed: {r.status_code} {r.text}")
    print(f"Created: {title}")

# ---------- Utility ----------
def make_key(title, source):
    h = hashlib.md5()
    h.update((title.strip() + "|" + source.strip()).encode("utf-8"))
    return h.hexdigest()

def clean(text):
    return re.sub(r"\s+", " ", (text or "").strip())

def pick_first(soup, selectors):
    for sel in selectors:
        el = soup.select_one(sel)
        if el and clean(el.get_text()):
            return el
    return None

def many_blocks(soup, selectors):
    for sel in selectors:
        els = soup.select(sel)
        if els:
            return els
    return []

# ---------- Generic extractors (robust fallbacks) ----------
def extract_entries_generic(soup):
    """
    Fallback extractor if a site changes layout.
    Returns list of dicts with keys: title, date, summary
    """
    entries = []
    blocks = many_blocks(soup, [
        "article", "li", "div.card", "div.item", "div.teaser", "div.news-item", "div.result", "div.post"
    ])
    for b in blocks:
        title_el = pick_first(b, ["h2", "h3", "a[title]", "a"])
        date_el  = pick_first(b, ["time", ".date", ".news-date", "span.date"])
        sum_el   = pick_first(b, ["p", ".summary", ".teaser", ".excerpt"])

        title = clean(title_el.get_text() if title_el else "")
        if not title:
            continue
        # heuristics: prefer case-looking titles
        looks_like_case = " v. " in title or " v." in title or " vs " in title.lower()
        if not looks_like_case:
            # keep if strongly AI/IP relevant
            if not any(k in title.lower() for k in ["ai", "copyright", "midjourney", "openai", "anthropic", "meta", "suno", "udio"]):
                continue

        date = clean(date_el.get_text() if date_el else "")
        summary = clean(sum_el.get_text() if sum_el else "")
        entries.append({"title": title, "date": date, "summary": summary})
    return entries

def infer_outcome_short(text):
    m = re.search(r"(fair use|summary judgment|dismiss(ed)?|prelim(inary)? injunction|injunction|settle(d)?|class action|stay(ed)?)", text, re.I)
    return clean(m.group(0)).capitalize() if m else "Update"

# ---------- Per-source scrapers ----------
def get_html(url):
    return requests.get(url, timeout=TIMEOUT).text

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

SOURCES = [scrape_mckool, scrape_bakerhostetler, scrape_wired, scrape_mishcon, scrape_cms]

# ---------- Issue body/format ----------
def make_issue_body(entry, key_hex):
    headline = entry["title"]
    date_line = f"**Date/Update**: {entry.get('date') or 'N/A'}"
    outcome_line = f"**Outcome (short)**: {entry.get('outcome') or 'Update'}"
    src = entry.get("source", "Unknown")
    url = entry.get("url", "")
    summary = entry.get("summary") or "Summary coming soon."

    # Format in your simple style
    body = f"""{headline}

{date_line}
{outcome_line}
**Source:** {src} ({url})

**Summary:** {summary}

**Key takeaway:** _Add takeaway once confirmed._

<!-- KEY: {key_hex} -->
"""
    return body

def run():
    # Gather entries from all sources, tolerate failures per source
    all_entries = []
    for fn in SOURCES:
        try:
            all_entries.extend(fn())
        except Exception as e:
            print(f"[WARN] {fn.__name__} failed: {e}")

    # Prepare labels
    source_labels = {SOURCE_LABEL_PREFIX + e["source"] for e in all_entries if e.get("source")}
    ensure_labels(list(source_labels))

    # De-duplication via stored keys
    existing = list_existing_issue_keys()

    created = 0
    for e in all_entries:
        src = e.get("source", "Unknown")
        key_hex = make_key(e["title"], src)
        if key_hex in existing:
            continue

        labels = COMMON_LABELS + [SOURCE_LABEL_PREFIX + src]
        body = make_issue_body(e, key_hex)
        title = e["title"]
        try:
            create_issue(title, body, labels)
            created += 1
        except Exception as ex:
            print(f"[ERROR] Could not create issue for '{title}': {ex}")

    print(f"Done. Created {created} new issue(s).")

if __name__ == "__main__":
    run()

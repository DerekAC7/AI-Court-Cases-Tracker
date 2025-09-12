import os
import re
import requests
from bs4 import BeautifulSoup

# ====== CONFIG ======
REPO_OWNER = "DerekAC7"
REPO_NAME  = "AI-Court-Cases-Tracker"
TOKEN = os.getenv("PERSONAL_ACCESS_TOKEN")  # provided by Actions from secrets.PAT_TOKEN
LABELS = ["AI Training", "Court Case", "Watcher"]  # created if missing
# ====================

API_BASE = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}"

def _gh_headers():
    if not TOKEN:
        raise RuntimeError("Missing token. In your workflow, map secrets.PAT_TOKEN to PERSONAL_ACCESS_TOKEN.")
    return {
        "Authorization": f"token {TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "ai-cases-tracker"
    }

# ----- GitHub helpers -----
def ensure_labels():
    # create labels if they don't exist
    r = requests.get(f"{API_BASE}/labels?per_page=100", headers=_gh_headers())
    r.raise_for_status()
    existing = {lbl["name"] for lbl in r.json()}
    for name in LABELS:
        if name not in existing:
            requests.post(f"{API_BASE}/labels", headers=_gh_headers(), json={"name": name})

def list_existing_issue_titles():
    # open + recently closed to reduce dupes
    titles = set()
    for state in ("open", "closed"):
        page = 1
        while True:
            r = requests.get(f"{API_BASE}/issues", headers=_gh_headers(),
                             params={"state": state, "per_page": 100, "page": page})
            r.raise_for_status()
            items = r.json()
            if not items: break
            for it in items:
                if "pull_request" in it:  # skip PRs
                    continue
                titles.add(it["title"].strip())
            page += 1
    return titles

def create_issue(title, body, labels=None):
    payload = {"title": title, "body": body}
    if labels:
        payload["labels"] = labels
    r = requests.post(f"{API_BASE}/issues", headers=_gh_headers(), json=payload)
    if r.status_code != 201:
        raise RuntimeError(f"Issue create failed: {r.status_code} {r.text}")
    print(f"Created: {title}")

# ----- Scrapers -----
def fetch_mckool_cases():
    """
    Best-effort scraper for McKool Smith AI Litigation page.
    NOTE: Sites change. If nothing is found, adjust CSS selectors below.
    """
    url = "https://www.mckoolsmith.com/newsroom-ailitigation"
    html = requests.get(url, timeout=30).text
    soup = BeautifulSoup(html, "html.parser")

    cases = []
    # Try a few common structures
    blocks = (
        soup.select("article") or
        soup.select("div.news-item, div.item, li, div.card")
    )

    for b in blocks:
        title_el = b.select_one("h2, h3, a[title], a")
        date_el  = b.select_one("time, .date, .news-date, span.date")
        para_el  = b.select_one("p")  # first paragraph/summary

        title = (title_el.get_text(strip=True) if title_el else "").strip()
        date  = (date_el.get_text(strip=True) if date_el else "").strip()
        summary = (para_el.get_text(" ", strip=True) if para_el else "").strip()

        # crude filter: keep likely case lines (contain "v." or "v ")
        if not title:
            continue
        if " v. " not in title and " v. " not in title.replace("v.", " v. "):
            # keep items that still look relevant (fallback)
            if "AI" not in title and "Artificial" not in title and "copyright" not in title.lower():
                continue

        # simple outcome guess from text (you can refine later)
        outcome = ""
        m = re.search(r"(summary judgment|dismiss(ed)?|settle(d)?|injunction|fair use|class action)",
                      f"{title} {summary}", flags=re.I)
        if m:
            outcome = m.group(0).strip().capitalize()

        cases.append({
            "title": title,
            "date": date,
            "outcome": outcome or "Update",
            "summary": summary
        })

    return cases

# ----- Formatting -----
def format_issue_body(case):
    """
    Your requested simple style. Edit to match exactly how you want it to read.
    """
    # Title line suggestion if you want to mirror your examples in body too:
    # e.g., "Bartz v. Anthropic – N.D. Cal rules AI training fair use."
    headline = case["title"]
    date_line = f"**Date/Update**: {case['date'] or 'N/A'}"
    outcome_line = f"**Outcome (short)**: {case['outcome']}"
    summary = case["summary"] or "Summary coming soon."

    body = f"""{headline}

{date_line}
{outcome_line}

**Summary:** {summary}

**Key takeaway:** _Add takeaway once confirmed._
"""
    return body

def main():
    ensure_labels()
    existing_titles = list_existing_issue_titles()

    # 1) McKool Smith
    for case in fetch_mckool_cases():
        title = case["title"].strip()
        if title in existing_titles:
            continue
        body = format_issue_body(case)
        create_issue(title, body, labels=LABELS)

if __name__ == "__main__":
    main()

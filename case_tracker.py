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

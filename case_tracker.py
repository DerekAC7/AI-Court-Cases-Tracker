#!/usr/bin/env python3
import os, requests, json, re, time

# ------------------------
# CONFIGURATION
# ------------------------
CL_API = "https://www.courtlistener.com/api/rest/v4/dockets/"
CL_DEs = "https://www.courtlistener.com/api/rest/v4/docket-entries/"
OUTPUT = "docs/cases.json"
DOCKET_ENTRIES_PER_CASE = 8

# Search queries focused on AI/IP cases
QUERIES = [
    "training AND copyright",
    "dataset AND copyright",
    "ai AND copyright",
    "llm AND copyright",
    "ai AND right of publicity",
    "ai AND dmca",
]

# CourtListener API token
TOKEN = os.environ.get("CL_API_TOKEN")
HEADERS = {"Authorization": f"Token {TOKEN}"} if TOKEN else {}

# ------------------------
# HELPERS
# ------------------------
def fetch(url, params=None):
    """Fetch JSON from CourtListener with retry."""
    for attempt in range(3):
        r = requests.get(url, headers=HEADERS, params=params, timeout=30)
        if r.status_code == 429:  # rate limited
            wait = int(r.headers.get("Retry-After", 3))
            print(f"[http] rate limited, sleeping {wait}s...", flush=True)
            time.sleep(wait)
            continue
        if r.status_code >= 400:
            r.raise_for_status()
        return r.json()
    raise RuntimeError(f"[http] Failed after retries: {url}")

def clean(txt):
    return re.sub(r"\s+", " ", txt or "").strip()

def status_from_text(text):
    if re.search(r"settled|resolved", text, re.I):
        return "Settled"
    if re.search(r"dismiss|denied", text, re.I):
        return "Dismissed"
    if re.search(r"judgment|granted|ruled", text, re.I):
        return "Judgment"
    return "Open/Active"

def outcome_short(text):
    if re.search(r"class action", text, re.I):
        return "Class action"
    if re.search(r"motion to dismiss", text, re.I):
        return "Motion to Dismiss"
    if re.search(r"prelim injunction", text, re.I):
        return "Preliminary Injunction"
    return "Update"

def is_ai_ip_related(caption, text):
    """Heuristic filter for relevance to AI/IP."""
    keywords = ["AI", "artificial intelligence", "training", "dataset", "copyright", "generative", "LLM", "DMCA", "midjourney", "stability", "meta", "openai", "anthropic"]
    haystack = f"{caption} {text}".lower()
    return any(k.lower() in haystack for k in keywords)

# ------------------------
# FETCH DOCKET ENTRIES
# ------------------------
def docket_entries(docket_id, limit=DOCKET_ENTRIES_PER_CASE):
    """Fetch docket entries for a given docket. Gracefully skips 401/403/404."""
    try:
        data = fetch(
            CL_DEs,
            {"docket": docket_id, "order_by": "date_filed desc", "page_size": limit},
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

# ------------------------
# MAIN GATHER FUNCTION
# ------------------------
def gather_from_dockets():
    results = []
    for q in QUERIES:
        print(f"[tracker] query: {q}", flush=True)
        page = 1
        while True:
            data = fetch(CL_API, {"search": q, "order_by": "date_filed desc", "page_size": 50, "page": page})
            for d in data.get("results", []):
                docket_id = d.get("id")
                caption = clean(d.get("case_name", ""))
                court = d.get("court", {}).get("full_name", "")
                entries = docket_entries(docket_id)
                text_blob = " ".join([clean(e.get("description") or e.get("entry_text") or "") for e in entries])

                # Keep case even if no entries available
                if not is_ai_ip_related(caption, text_blob):
                    pass  # keep anyway, rely on search relevance

                status = status_from_text(text_blob) if entries else "Open/Active"
                outcome = outcome_short(text_blob)

                results.append({
                    "title": f"{caption} – {court}" if court else caption,
                    "status": status,
                    "summary": text_blob[:500] + ("..." if len(text_blob) > 500 else ""),
                    "source": "CourtListener",
                    "url": d.get("absolute_url"),
                    "outcome": outcome,
                })
            if not data.get("next"):
                break
            page += 1
    return results

# ------------------------
# ENTRY POINT
# ------------------------
def run():
    print("[tracker] starting CourtListener crawl", flush=True)
    items = gather_from_dockets()
    print(f"[tracker] collected {len(items)} cases", flush=True)
    os.makedirs("docs", exist_ok=True)
    with open(OUTPUT, "w") as f:
        json.dump(items, f, indent=2)
    print(f"[tracker] wrote {OUTPUT}", flush=True)

if __name__ == "__main__":
    run()

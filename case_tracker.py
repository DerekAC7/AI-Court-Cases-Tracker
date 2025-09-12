import os, re, json, requests
from datetime import datetime

REPO_OWNER = "DerekAC7"
REPO_NAME  = "AI-Court-Cases-Tracker"
TOKEN = os.getenv("PERSONAL_ACCESS_TOKEN")
DOCS_DIR = "docs"
JSON_PATH = f"{DOCS_DIR}/cases.json"

CL_SEARCH = "https://www.courtlistener.com/api/rest/v4/search/"
CL_DEs    = "https://www.courtlistener.com/api/rest/v4/docket-entries/"

TARGET_PARTIES = [
    "OpenAI","Anthropic","Meta","Midjourney","Stability AI","Suno","Udio",
    "Reddit","Disney","Getty","New York Times","Authors Guild",
    "Universal Music","Warner Music","Sony Music"
]
AI_KEYWORDS = ["AI","artificial intelligence","generative","LLM","model","training","dataset","copyright"]

def fetch(url, params=None):
    r = requests.get(url, params=params, timeout=40)
    r.raise_for_status()
    return r.json()

def build_query():
    party_q = " OR ".join([f'"{p}"' for p in TARGET_PARTIES])
    kw_q    = " OR ".join([f'"{k}"' for k in AI_KEYWORDS])
    return {"q": f"({party_q}) ({kw_q})", "type": "dockets", "page_size": 50, "order_by": "dateFiled desc"}

def status_from_text(t):
    t=t.lower()
    if "summary judgment" in t: return "Judgment"
    if "dismiss" in t: return "Dismissed"
    if "injunction" in t: return "Injunction"
    if "settle" in t: return "Settled"
    if "class certific" in t: return "Class certified"
    return "Open/Active"

def outcome_short(t):
    m = re.search(r"(fair use|summary judgment|dismiss(ed)?|injunction|settle(d)?|class certification)", t or "", re.I)
    return m.group(0).capitalize() if m else "Update"

def music_publisher_takeaway(t):
    t=t.lower()
    if "fair use" in t and "pirated" in t:
        return "Even if training is fair use, dataset acquisition can still create liability. Publishers should watch provenance."
    if "fair use" in t and "market" in t:
        return "Courts weigh harm to the market for the original works more than a separate 'training license' market."
    if "dismiss" in t:
        return "Complaints that don't connect copying to market harm risk dismissal."
    if "settle" in t:
        return "Parties are resolving AI/IP disputes without merits rulings — use settlement patterns to benchmark licenses."
    return ""

def latest_entries(docket_id):
    data = fetch(CL_DEs, {"docket": docket_id, "page_size": 6, "order_by": "date_filed desc"})
    return data.get("results", [])

def search_dockets():
    results=[]
    url,params = CL_SEARCH, build_query()
    while True:
        data = fetch(url, params)
        for row in data.get("results", []):
            if row.get("result_type")!="docket": continue
            d=row.get("docket") or {}
            caption = d.get("caption") or row.get("caseName")
            if not caption: continue
            docket_id = d.get("id")
            filed = d.get("date_filed") or ""
            entries = latest_entries(docket_id)
            text_blob = " ".join([e.get("description","") for e in entries])
            status = status_from_text(text_blob)
            outcome = outcome_short(text_blob)
            takeaway = music_publisher_takeaway(text_blob)
            summary = "; ".join([e.get("description","") for e in entries[:3]]) or "No recent entries."
            results.append({
                "title": caption,
                "date": filed,
                "summary": summary,
                "source": "CourtListener/RECAP",
                "url": f"https://www.courtlistener.com/docket/{docket_id}/",
                "outcome": outcome,
                "status": status,
                "takeaway": takeaway
            })
        if not data.get("next"): break
        url,params = data["next"],None
    return results

def ensure_docs():
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(os.path.join(DOCS_DIR, ".nojekyll"), "w") as f: f.write("")
    with open(os.path.join(DOCS_DIR, "index.html"), "w") as f: f.write("<h1>AI Court Cases Tracker</h1><p>Site auto-generated.</p>")

def run():
    items = search_dockets()
    ensure_docs()
    with open(JSON_PATH, "w") as f:
        json.dump(items, f, indent=2)
    print(f"Wrote {len(items)} cases to {JSON_PATH}")

if __name__=="__main__":
    run()

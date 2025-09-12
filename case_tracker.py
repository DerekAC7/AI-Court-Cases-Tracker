import os
import requests
from bs4 import BeautifulSoup

# Load your token from the environment (set in GitHub Actions as PAT_TOKEN)
GITHUB_TOKEN = os.getenv("PERSONAL_ACCESS_TOKEN")
REPO_OWNER = "your_github_username"  # Replace with your GitHub username
REPO_NAME = "AI-Court-Cases-Tracker"  # Make sure this matches your repo name

def fetch_case_data():
    url = "https://www.mckoolsmith.com/newsroom-ailitigation"  # Example public tracker
    response = requests.get(url)
    soup = BeautifulSoup(response.text, "html.parser")

    cases = []
    # This CSS selector needs to be adjusted to match the actual site structure.
    # Inspect the site to get the correct tags/classes.
    for case in soup.find_all("div", class_="case-summary"):
        case_title = case.find("h2").text.strip()
        case_date = case.find("span", class_="date").text.strip()
        case_outcome = case.find("p", class_="outcome").text.strip()

        cases.append({
            "title": case_title,
            "date": case_date,
            "outcome": case_outcome
        })

    return cases

def create_github_issue(title, body):
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/issues"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }
    data = {
        "title": title,
        "body": body
    }

    response = requests.post(url, json=data, headers=headers)
    if response.status_code == 201:
        print(f"Issue created successfully: {title}")
    else:
        print(f"Failed to create issue: {response.status_code}, {response.text}")

def create_issues_for_cases(cases):
    for case in cases:
        title = case["title"]
        body = f"""
**Date Filed**: {case['date']}
**Outcome**: {case['outcome']}
**Key Takeaway**: [Add summary takeaway based on the case]
"""
        create_github_issue(title, body)

if __name__ == "__main__":
    if not GITHUB_TOKEN:
        raise RuntimeError("No GitHub token found. Make sure PAT_TOKEN is set in repository secrets.")
    cases = fetch_case_data()
    create_issues_for_cases(cases)

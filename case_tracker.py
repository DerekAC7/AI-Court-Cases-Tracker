import requests
from bs4 import BeautifulSoup

def fetch_case_data():
    url = 'https://www.mckoolsmith.com/newsroom-ailitigation'  # Example URL
    response = requests.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')

    cases = []
    for case in soup.find_all('div', class_='case-summary'):  # Adjust this selector to match your page
        case_title = case.find('h2').text.strip()
        case_date = case.find('span', class_='date').text.strip()
        case_outcome = case.find('p', class_='outcome').text.strip()

        cases.append({
            'title': case_title,
            'date': case_date,
            'outcome': case_outcome
        })
    return cases

def create_github_issue(title, body):
    GITHUB_TOKEN = 'your_personal_access_token'  # Replace with your GitHub token
    REPO_OWNER = 'your_github_username'  # Replace with your GitHub username
    REPO_NAME = 'AI-Court-Cases-Tracker'  # Replace with your repository name

    url = f'https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/issues'
    headers = {
        'Authorization': f'token {GITHUB_TOKEN}',
        'Content-Type': 'application/json'
    }
    data = {
        'title': title,
        'body': body
    }

    response = requests.post(url, json=data, headers=headers)
    if response.status_code == 201:
        print(f'Issue created successfully: {title}')
    else:
        print(f'Failed to create issue: {response.text}')

def create_issues_for_cases(cases):
    for case in cases:
        title = case['title']
        body = f"""
        **Date Filed**: {case['date']}
        **Outcome**: {case['outcome']}
        **Key Takeaway**: [Summary takeaway based on the case]
        """
        create_github_issue(title, body)

if __name__ == "__main__":
    cases = fetch_case_data()
    create_issues_for_cases(cases)

import requests

token = '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'
headers = {
    'Authorization': f'token {token}',
    'Accept': 'application/vnd.github.v3+json'
}
url = 'https://api.github.com/repos/fakhirmr/ihsg-system/actions/runs'

response = requests.get(url, headers=headers)
if response.status_code == 200:
    runs = response.json().get('workflow_runs', [])
    print(f'Found {len(runs)} runs. Showing top 10:')
    for run in runs[:10]:
        print(f"- {run['name']} | Status: {run['status']} | Conclusion: {run['conclusion']} | Updated: {run['updated_at']} | URL: {run['html_url']}")
else:
    print(f'Error: {response.status_code} - {response.text}')

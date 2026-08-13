"""
One-off diagnostic: checks whether PSX's company profile page includes the
"Company Profile" section (WEBSITE, ADDRESS, etc.) in a plain HTTP GET, or
whether that section only appears after JavaScript runs in a real browser
(same pattern /calendar and /announcements turned out to have earlier in
this project).

Run: python3 diagnose_company_page.py PSO
"""
import sys
import requests

symbol = sys.argv[1] if len(sys.argv) > 1 else "PSO"
url = f"https://dps.psx.com.pk/company/{symbol}"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
}

print(f"Fetching {url} ...")
resp = requests.get(url, headers=headers, timeout=30)
html = resp.text

print(f"\nStatus: {resp.status_code}")
print(f"Response length: {len(html)} characters")
print(f"Contains 'WEBSITE': {'WEBSITE' in html}")
print(f"Contains 'item__head': {'item__head' in html}")
print(f"Contains 'profile__item': {'profile__item' in html}")
print(f"Contains 'Company Profile': {'Company Profile' in html}")

if "WEBSITE" in html:
    idx = html.index("WEBSITE")
    print(f"\nContext around 'WEBSITE' match:\n{html[max(0, idx-200):idx+300]}")
else:
    print("\n'WEBSITE' not found anywhere in the response. This confirms the "
          "Company Profile section loads via a separate JavaScript/AJAX call, "
          "same pattern as /calendar and /announcements earlier in this project. "
          "Next step: DevTools > Network tab > Fetch/XHR, load this company page, "
          "and find the request that returns the profile data.")

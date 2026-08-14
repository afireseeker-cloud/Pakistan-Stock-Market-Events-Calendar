"""
Parses the "Financials" section of a PSX company profile page -- real
Annual and Quarterly Sales/Profit-after-Taxation/EPS data, sourced from
Capital Stake (a third-party financial data provider PSX licenses this
table from -- see their own Terms of Use, a separate consideration from
PSX's own data-license notice flagged earlier in this project).

Confirmed real markup (live capture): both Annual and Quarterly tables are
fully server-rendered in the same page load -- the "tab" toggle is just
CSS/JS show/hide, not a second request. The Quarterly table's last column
is deliberately the same quarter one year prior (e.g. "Q3 2026" alongside
"Q3 2025"), which PSX has already computed for us -- no extra work needed
to build a YoY comparison pair.
"""
import re

from bs4 import BeautifulSoup


def parse_number(text: str) -> float | None:
    text = text.strip().replace(",", "")
    if not text or text in ("-", "N/A"):
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    try:
        value = float(text)
        return -value if negative else value
    except ValueError:
        return None


def parse_financials_table(panel) -> dict:
    """Returns {period_label: {row_label: value}}, e.g.
    {'2025': {'Sales': ..., 'Profit after Taxation': ..., 'EPS': 44.54}}"""
    if panel is None:
        return {}
    table = panel.find("table")
    if table is None:
        return {}

    header_cells = table.select("thead th")
    periods = [th.get_text(strip=True) for th in header_cells[1:]]  # skip the blank first <th>

    row_values = {}
    for tr in table.select("tbody tr"):
        cells = tr.find_all("td")
        if not cells:
            continue
        row_label = cells[0].get_text(strip=True)
        values = [parse_number(td.get_text(strip=True)) for td in cells[1:]]
        row_values[row_label] = values

    # transpose rows-by-label into periods-by-label, preserving column order
    # (order matters -- the first column is always the most recent period)
    result = {}
    for i, period in enumerate(periods):
        result[period] = {label: values[i] for label, values in row_values.items() if i < len(values)}
    return result


def extract_financials(soup: BeautifulSoup) -> dict | None:
    container = soup.find("div", class_="company__financials")
    if container is None:
        return None
    annual_panel = container.select_one('.tabs__panel[data-name="Annual"]')
    quarterly_panel = container.select_one('.tabs__panel[data-name="Quarterly"]')
    return {
        "annual": parse_financials_table(annual_panel),
        "quarterly": parse_financials_table(quarterly_panel),
    }


def compute_eps_summary(financials: dict) -> dict:
    """Reduces the full financials dict to the handful of fields the
    calendar actually displays: latest EPS and a fair prior-period
    comparison, both annual and quarterly (quarterly preferred as the more
    timely comparison when available)."""
    annual = financials.get("annual", {})
    quarterly = financials.get("quarterly", {})

    annual_years = sorted((y for y in annual if y.isdigit()), reverse=True)
    latest_annual_eps = annual.get(annual_years[0], {}).get("EPS") if annual_years else None
    prior_annual_eps = annual.get(annual_years[1], {}).get("EPS") if len(annual_years) > 1 else None

    quarter_labels = list(quarterly.keys())  # preserves original column order, latest first
    latest_q_label = quarter_labels[0] if quarter_labels else None
    latest_quarterly_eps = quarterly.get(latest_q_label, {}).get("EPS") if latest_q_label else None

    yoy_quarterly_eps = None
    if latest_q_label:
        m = re.match(r"(Q\d)\s+(\d{4})", latest_q_label)
        if m:
            qtr, year = m.group(1), int(m.group(2))
            target_label = f"{qtr} {year - 1}"
            yoy_quarterly_eps = quarterly.get(target_label, {}).get("EPS")

    return {
        "latest_annual_eps": latest_annual_eps,
        "prior_annual_eps": prior_annual_eps,
        "latest_quarterly_eps": latest_quarterly_eps,
        "latest_quarterly_label": latest_q_label,
        "yoy_quarterly_eps": yoy_quarterly_eps,
    }


if __name__ == "__main__":
    with open("fixtures/financials_sample.html") as f:
        soup = BeautifulSoup(f.read(), "lxml")
    financials = extract_financials(soup)
    import json
    print(json.dumps(financials, indent=2))
    print()
    print("EPS summary:", json.dumps(compute_eps_summary(financials), indent=2))

import logging
import re
import requests
import json
from datetime import datetime
from urllib.parse import urljoin
from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

SOCOM_BAA_URL = "https://www.socom.mil/SOF-ATL/Pages/baa.aspx"
BASE_URL = "https://www.socom.mil"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
}


def fetch_socom_opportunities(max_items: int = 100) -> list[dict[str, str]]:
    logger.info(f"Scraping {SOCOM_BAA_URL}")
    results = []

    try:
        response = requests.get(SOCOM_BAA_URL, headers=HEADERS, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        table = soup.find('table', summary="BAA, RFI, & CSO Announcement Board") or \
                soup.find('table', class_="ms-listviewtable")

        if not isinstance(table, Tag):
            logger.error("SOCOM BAA table not found.")
            return results

        rows = [row for row in table.find_all('tr', class_='ms-itmHoverEnabled') if isinstance(row, Tag)]
        logger.info(f"Found {len(rows)} rows.")

        for row in rows:
            if len(results) >= max_items:
                break

            cells = [cell for cell in row.find_all('td', class_='ms-cellstyle') if isinstance(cell, Tag)]
            if len(cells) < 4:
                continue

            title = cells[0].get_text(strip=True)

            link_tag = cells[1].find('a') if isinstance(cells[1], Tag) else None
            if link_tag and isinstance(link_tag, Tag) and link_tag.has_attr('href'):
                href_val = str(link_tag.get('href') or '').strip()
                if not href_val or any(term in href_val.lower() for term in ["_archive/", "sub-form.aspx", "template.", "instructions"]):
                    continue
                opportunity_url = urljoin(BASE_URL, href_val)
            else:
                continue

            end_date_raw = cells[3].get_text(strip=True)
            close_date = "N/A"
            match = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", end_date_raw)
            if match:
                try:
                    close_date = datetime.strptime(match.group(1), "%m/%d/%Y").strftime("%Y-%m-%d")
                except ValueError:
                    close_date = end_date_raw

            results.append({
                "Source": "SOCOM BAA",
                "Title": title,
                "Description": f"Details for '{title}'. Close Date: {end_date_raw}.",
                "URL": opportunity_url,
                "Close Date": close_date,
                "ScrapedDate": datetime.now().isoformat()
            })

    except requests.RequestException as e:
        logger.error(f"Request error: {e}", exc_info=True)

    logger.info(f"Finished. Scraped {len(results)} items.")
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    try:
        scraped = fetch_socom_opportunities(max_items=5)
        print(json.dumps(scraped, indent=2))
    except Exception as e:
        logger.error(f"Unexpected error in standalone run: {e}", exc_info=True)

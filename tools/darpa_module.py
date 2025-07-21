import requests
import logging
from urllib.parse import urljoin
from datetime import datetime
import re
import json
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(funcName)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

DARPA_BASE_URL = "https://www.darpa.mil/work-with-us/opportunities"
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

def parse_darpa_date(date_text):
    if not date_text or not date_text.strip():
        return "N/A"
    try:
        for fmt in ("%B %d %Y", "%b %d %Y", "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(date_text.strip(), fmt)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue
    except Exception:
        pass
    return "N/A"

def fetch_darpa_opportunities():
    logger.info(f"[DARPA Module] Fetching from {DARPA_BASE_URL}")
    results = []
    try:
        resp = requests.get(DARPA_BASE_URL, headers=DEFAULT_HEADERS, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        cards = soup.select("div[style*='box-shadow'][class*='bg-white']")
        logger.info(f"Found {len(cards)} cards on DARPA site.")

        for card in cards:
            link_tag = card.select_one("a[href*='sam.gov'], a[href*='grants.gov']")
            title_tag = card.select_one("h4")
            if link_tag:
                link_url = urljoin(DARPA_BASE_URL, str(link_tag.get('href')))
                title = title_tag.get_text(strip=True) if title_tag else "Title Not Found"

                results.append({
                    "Source": "DARPA",
                    "Title": title,
                    "Description": f"Details available at {link_url}",
                    "URL": link_url,
                    "Close Date": "See URL",
                    "ScrapedDate": datetime.now().isoformat()
                })
    except Exception as e:
        logger.error(f"Error scraping DARPA page: {e}", exc_info=True)

    logger.info(f"[DARPA Module] Finished with {len(results)} results.")
    return results

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    data = fetch_darpa_opportunities()
    print(json.dumps(data, indent=2))

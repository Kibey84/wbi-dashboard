import time
import logging
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from datetime import datetime
import json

logger = logging.getLogger(__name__)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    _handler.setFormatter(_formatter)
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}

def _parse_grants_date(date_str):
    if not date_str or not date_str.strip():
        return "N/A"
    try:
        return datetime.strptime(date_str.strip(), "%m/%d/%Y").strftime("%Y-%m-%d")
    except ValueError:
        logger.warning(f"Could not parse date: {date_str}")
        return date_str

def fetch_grantsgov_opportunities():
    base_url = "https://www.grants.gov"
    search_url = "https://simpler.grants.gov/search/"
    results = []

    try:
        response = requests.get(search_url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        list_items = soup.select("ul.usa-list > li")
        logger.info(f"Found {len(list_items)} total opportunities.")

        for item in list_items:
            try:
                title_tag = item.select_one("h3 a")
                if not title_tag:
                    continue

                title = title_tag.get_text(strip=True)
                detail_url = urljoin(base_url, str(title_tag.get("href") or ""))

                meta_items = item.select("div.g-meta > div")
                meta_dict = {}
                for meta in meta_items:
                    label = meta.select_one("span.g-label")
                    value = meta.select_one("span.g-value")
                    if label and value:
                        meta_dict[label.get_text(strip=True).lower()] = value.get_text(strip=True)

                status = meta_dict.get("status", "").lower()
                if "posted" not in status:
                    continue

                opp_number = meta_dict.get("opportunity #", "")
                agency = meta_dict.get("agency", "")
                close_date = _parse_grants_date(meta_dict.get("close date"))
                open_date = _parse_grants_date(meta_dict.get("post date"))

                logger.info(f"✅ Scraped Opp#: {opp_number} - '{title[:60]}'")
                results.append({
                    "Source": "Grants.gov",
                    "Title": title,
                    "Description": f"Agency: {agency} | Opportunity Number: {opp_number}",
                    "URL": detail_url,
                    "ScrapedDate": datetime.now().isoformat(),
                    "Open Date": open_date,
                    "Close Date": close_date
                })

            except Exception as e:
                logger.warning(f"Error processing item: {e}")

    except Exception as e:
        logger.error(f"Failed to fetch Grants.gov page: {e}", exc_info=True)

    logger.info(f"Finished. Found {len(results)} posted opportunities.")
    return results

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    scraped = fetch_grantsgov_opportunities()
    print(f"\n--- Scraped {len(scraped)} Grants.gov Opportunities ---")
    for opp in scraped:
        print(json.dumps(opp, indent=2))

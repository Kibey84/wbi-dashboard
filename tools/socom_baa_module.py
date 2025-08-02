import httpx
from bs4 import BeautifulSoup, Tag
import logging
from datetime import datetime
import time
import re
import json
from urllib.parse import urljoin
from typing import List, Dict

# --- Logger Setup ---
logger = logging.getLogger(__name__)

# --- Module Configuration ---
SOCOM_BAA_URL = "https://www.socom.mil/SOF-ATL/Pages/baa.aspx"
BASE_URL = "https://www.socom.mil"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
}

def fetch_socom_opportunities(max_items: int = 100) -> List[Dict[str, str]]:
    """
    Fetches SOCOM BAA opportunities with retry logic for increased reliability.
    """
    logger.info(f"Scraping {SOCOM_BAA_URL}")
    results = []
    max_retries = 3

    for attempt in range(max_retries):
        try:
            with httpx.Client(headers=HEADERS, timeout=30.0, follow_redirects=True) as client:
                response = client.get(SOCOM_BAA_URL)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")

                table = soup.find('table', summary="BAA, RFI, & CSO Announcement Board") or \
                        soup.find('table', class_="ms-listviewtable")

                if not isinstance(table, Tag):
                    logger.error("SOCOM BAA table not found. The site structure may have changed.")
                    return results

                rows = table.find_all('tr', class_='ms-itmHoverEnabled')
                logger.info(f"Found {len(rows)} rows.")

                for row in rows:
                    if len(results) >= max_items:
                        logger.info(f"Reached max_items limit of {max_items}.")
                        break

                    if not isinstance(row, Tag):
                        continue

                    cells = row.find_all('td', class_='ms-cellstyle')
                    if len(cells) < 4:
                        continue

                    title = cells[0].get_text(strip=True)

                    cell_with_link = cells[1]
                    if not isinstance(cell_with_link, Tag):
                        continue
                    
                    link_tag = cell_with_link.find('a')
                    if isinstance(link_tag, Tag) and link_tag.has_attr('href'):
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
                
                return results

        except httpx.RequestError as e:
            logger.error(f"Attempt {attempt + 1} of {max_retries} failed for SOCOM page: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt) 
            else:
                logger.error("All retries failed for SOCOM.")
        except Exception as e:
            logger.error(f"An unexpected error occurred: {e}", exc_info=True)
            break 

    logger.info(f"Finished. Scraped {len(results)} items.")
    return results

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    try:
        scraped = fetch_socom_opportunities(max_items=5)
        print(json.dumps(scraped, indent=2))
    except Exception as e:
        logger.error(f"Unexpected error in standalone run: {e}", exc_info=True)

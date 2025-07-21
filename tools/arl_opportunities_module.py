import requests
import logging
import json
from bs4 import BeautifulSoup
from datetime import datetime
import re

logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

def fetch_arl_opportunities(max_items=20):
    target_url = "https://cftste.experience.crmforce.mil/arlext/s/arl-opportunities"
    logger.info(f"🔍 Fetching ARL Opportunities from {target_url}")
    results = []
    
    try:
        response = requests.get(target_url, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        opportunity_cards = soup.select("div.slds-card")  
        logger.info(f"Found {len(opportunity_cards)} cards on page.")

        for idx, card in enumerate(opportunity_cards[:max_items]):
            try:
                title_el = card.select_one("h2.slds-card__header-title")
                title = title_el.get_text(strip=True) if title_el else "N/A"

                desc_el = card.select_one("div.slds-card__body")
                desc = desc_el.get_text(strip=True)[:1000] if desc_el else "N/A"

                ann_id_el = card.select_one("span.announcement-id")
                ann_id = ann_id_el.get_text(strip=True) if ann_id_el else "N/A"

                open_date_match = re.search(r"Open Date:\s*(\d{1,2}/\d{1,2}/\d{4})", card.get_text(), re.IGNORECASE)
                close_date_match = re.search(r"Close Date:\s*(\d{1,2}/\d{1,2}/\d{4})", card.get_text(), re.IGNORECASE)

                open_date = open_date_match.group(1) if open_date_match else "N/A"
                close_date = close_date_match.group(1) if close_date_match else "N/A"

                result = {
                    "Source": "ARL Opportunities",
                    "Title": title,
                    "Announcement ID": ann_id,
                    "Description": desc,
                    "Open Date": open_date,
                    "Close Date": close_date,
                    "ScrapedDate": datetime.now().isoformat(),
                    "URL": target_url
                }

                logger.info(f"✅ Scraped: {title[:50]}...")
                results.append(result)

            except Exception as e_card:
                logger.error(f"Error scraping card #{idx+1}: {e_card}")

    except Exception as e:
        logger.error(f"Error fetching ARL Opportunities: {e}")

    logger.info(f"✅ Finished fetching {len(results)} opportunities.")
    return results

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    opps = fetch_arl_opportunities(5)
    print(json.dumps(opps, indent=2))

import httpx
from bs4 import BeautifulSoup, Tag
from urllib.parse import urljoin
from datetime import datetime
import logging
import re
import json
import time
from typing import Optional, List, Dict

# --- Logger Setup ---
logger = logging.getLogger(__name__)

# --- Module Configuration ---
EUREKA_OPENCALLS_URL = "https://eurekanetwork.org/opencalls/"
REQUEST_DELAY_SECONDS = 0.5 

def _parse_eureka_date(date_str: str) -> str:
    """Parses a date string from 'DD Month YYYY' format."""
    if not date_str:
        return "N/A"
    try:
        return datetime.strptime(date_str, "%d %B %Y").strftime("%Y-%m-%d")
    except ValueError:
        logger.warning(f"[EUREKA Date Parser] Could not parse date: {date_str}")
        return date_str

def _fetch_eureka_detail_page(client: httpx.Client, detail_url: str, title_hint: str) -> Optional[Dict]:
    """Fetches and parses the detail page for a single opportunity."""
    try:
        response = client.get(detail_url)
        response.raise_for_status()
        detail_soup = BeautifulSoup(response.text, "html.parser")

        detail_title_el = detail_soup.select_one("h1.heading-xl, h1.font-bold, h2.call-title, h1.text-3xl, header.wp-block-post-title h1")
        final_title = detail_title_el.get_text(strip=True) if detail_title_el else title_hint

        close_date_str = "N/A"
        date_el = detail_soup.find(lambda tag: isinstance(tag, Tag) and "deadline:" in tag.get_text(strip=True).lower())
        if date_el:
            date_found = re.search(r"Deadline:\s*(\d{1,2}\s+\w+\s+\d{4})", date_el.get_text(strip=True), re.IGNORECASE)
            if date_found:
                parsed_date = _parse_eureka_date(date_found.group(1))
                try:
                    if datetime.strptime(parsed_date, "%Y-%m-%d") < datetime.now():
                        logger.info(f"Skipping expired opportunity: {final_title}")
                        return None
                    close_date_str = parsed_date
                except ValueError:
                    close_date_str = parsed_date 

        desc_el = detail_soup.select_one("div.prose, div.wysiwyg-content, article.post-content, div.entry-content, main#main")
        description = "Description not found."
        if desc_el:
            for unwanted in desc_el.select("nav, footer, header, aside, .sidebar, form, div[class*='share'], div[class*='related'], div[class*='meta']"):
                unwanted.decompose()
            description = ' '.join(desc_el.get_text(separator=" ", strip=True).split())

        return {
            "Source": "EUREKA",
            "Title": final_title,
            "Description": description,
            "URL": detail_url,
            "ScrapedDate": datetime.now().strftime("%Y-%m-%d"),
            "Close Date": close_date_str
        }

    except Exception as e:
        logger.error(f"Error processing detail page {detail_url}: {e}")
        return None

def fetch_eureka_opportunities() -> List[Dict]:
    logger.info(f"Fetching EUREKA opportunities from {EUREKA_OPENCALLS_URL}")
    opportunities = []
    max_retries = 3

    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=30.0, follow_redirects=True) as client:
                response = client.get(EUREKA_OPENCALLS_URL)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")

                card_links = soup.select("div.bg-white.group > a[href]")
                logger.info(f"Found {len(card_links)} open call links.")

                for idx, card_link in enumerate(card_links, 1):
                    href = card_link.get("href")
                    if not href or not isinstance(href, str):
                        continue
                    
                    full_url = urljoin(EUREKA_OPENCALLS_URL, href.strip())
                    title_el = card_link.select_one("h3.heading-sm, h2.heading-md")
                    title_hint = title_el.get_text(strip=True) if title_el else f"Open Call {idx}"

                    logger.info(f"Processing detail page: {full_url}")
                    detail_data = _fetch_eureka_detail_page(client, full_url, title_hint)
                    
                    if detail_data:
                        opportunities.append(detail_data)
                        logger.info(f"✅ Scraped opportunity: {detail_data['Title']}")
                    
                    time.sleep(REQUEST_DELAY_SECONDS)

                return opportunities 

        except httpx.RequestError as e:
            logger.error(f"Attempt {attempt + 1} failed for EUREKA page: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt) 
            else:
                logger.error("All retries failed for EUREKA.")
        except Exception as e:
            logger.error(f"Failed to fetch EUREKA page: {e}", exc_info=True)
            break 
    return opportunities

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    scraped = fetch_eureka_opportunities()
    print(f"\n--- Scraped {len(scraped)} EUREKA Opportunities ---")
    print(json.dumps(scraped, indent=2))

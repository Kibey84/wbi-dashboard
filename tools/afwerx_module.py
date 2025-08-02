import httpx
from bs4 import BeautifulSoup, Tag
from datetime import datetime
import logging
import time
import re
from urllib.parse import urljoin
import json

logger = logging.getLogger(__name__)

def fetch_afwerx_opportunities() -> list:
    """
    Fetches AFWERX opportunities using httpx with retry logic for increased stability.
    """
    base_url = "https://afwerxchallenge.com/current-efforts/"
    results = []
    max_retries = 3
    
    logger.info(f"Fetching AFWERX opportunities from {base_url}")

    for attempt in range(max_retries):
        try:
            with httpx.Client(follow_redirects=True, timeout=30.0) as client:
                response = client.get(base_url)
                response.raise_for_status()
                
                soup = BeautifulSoup(response.content, "html.parser")

                cards = soup.select('div.featured-content-card a[href]')
                logger.info(f"Found {len(cards)} potential AFWERX opportunity cards.")

                for card in cards:
                    href = card.get("href")
                    if not href or not isinstance(href, str):
                        continue
                    
                    full_url = urljoin(base_url, href.strip())

                    try:
                        detail_resp = client.get(full_url)
                        detail_resp.raise_for_status()
                        detail_soup = BeautifulSoup(detail_resp.content, "html.parser")

                        title_el = detail_soup.select_one('h1.title, h1.challenge-title, div.title-holder h1, h1.entry-title')
                        title = title_el.get_text(strip=True) if title_el else "No Title Found"

                        desc_el = detail_soup.select_one('div.challenge-description, div.description-content, section.overview-section, div.fr-view, article#main-content')
                        description = desc_el.get_text(separator=" ", strip=True) if desc_el else "No Description Found"

                        open_date_str, close_date_str = extract_dates_from_text(description)

                        results.append({
                            "Source": "AFWERX",
                            "Title": title,
                            "Description": description[:1500], 
                            "URL": full_url,
                            "Open Date": open_date_str,
                            "Close Date": close_date_str,
                            "ScrapedDate": datetime.now().isoformat()
                        })
                        logger.info(f"✅ Scraped AFWERX opportunity: {title}")

                    except httpx.RequestError as detail_err:
                        logger.error(f"Error fetching detail page {full_url}: {detail_err}")
                    except Exception as parse_err:
                        logger.error(f"Error parsing detail page {full_url}: {parse_err}")
                
                return results

        except httpx.RequestError as e:
            logger.error(f"Attempt {attempt + 1} of {max_retries} failed for AFWERX main page: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  
            else:
                logger.error("All retries failed for AFWERX. Returning empty list.")
                return []
        except Exception as e:
            logger.error(f"An unexpected error occurred while fetching AFWERX page: {e}", exc_info=True)
            return []
            
    return results

def extract_dates_from_text(text: str) -> tuple[str, str]:
    """
    Extracts open and close dates from a block of text using regex.
    """
    open_date = "N/A"
    close_date = "N/A"

    open_match = re.search(r"opens on\s+([A-Za-z]+\s+\d{1,2},\s+\d{4}|\d{1,2}/\d{1,2}/\d{4})", text, re.IGNORECASE)
    if open_match:
        open_date = open_match.group(1).strip()

    close_match = re.search(r"(?:closes on|ends on|due by)\s+([A-Za-z]+\s+\d{1,2},\s+\d{4}|\d{1,2}/\d{1,2}/\d{4})", text, re.IGNORECASE)
    if close_match:
        close_date = close_match.group(1).strip()

    return open_date, close_date

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    opportunities = fetch_afwerx_opportunities()
    print(f"\n--- Found {len(opportunities)} AFWERX opportunities ---")
    for opp in opportunities:
        print(json.dumps(opp, indent=2))

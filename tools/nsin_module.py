# nsin_module.py
import logging
import requests
import time
from bs4 import BeautifulSoup, Tag
from urllib.parse import urljoin
from datetime import datetime
import re
import json
from typing import Optional

# Selenium Imports
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.remote.webdriver import WebDriver


# Setup logger for this module
logger = logging.getLogger(__name__)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(funcName)s - %(message)s')
    _handler.setFormatter(_formatter)
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

# Default values for the module
MODULE_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
}

def _parse_date_from_nsin_line(line_text: str) -> Optional[datetime]:
    """
    Helper to parse a date from a line of text.
    """
    if not line_text: return None
    date_match = re.search(r"((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},\s*\d{4}|\d{1,2}/\d{1,2}/\d{2,4})", line_text, re.IGNORECASE)
    if date_match:
        date_str = date_match.group(1).replace(',', '')
        for fmt in ("%B %d %Y", "%b %d %Y", "%m/%d/%Y", "%m/%d/%y"):
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
    return None

def _parse_nsin_detail_page(detail_url: str, headers_to_use: dict):
    """
    Fetches and parses an NSIN detail page for title, description, open date, and closing date.
    Returns a tuple: (title, description, open_date_obj, closing_date_obj)
    """
    module_name = "NSIN Detail Parser"
    logger.debug(f"[{module_name}] Fetching details from: {detail_url}")
    title, description, open_date_obj, closing_date_obj = "Title Not Found", "Description Not Found", None, None

    try:
        page_response = requests.get(detail_url, headers=headers_to_use, timeout=25)
        page_response.raise_for_status()
        psoup = BeautifulSoup(page_response.text, "html.parser")

        title_el = psoup.select_one("h1.entry-title, h1.page-title, h1.title")
        if title_el:
            title = title_el.get_text(strip=True)
        elif psoup.title and psoup.title.string:
            title = psoup.title.string.strip()

        desc_el = psoup.select_one("div.entry-content, article.content, main#main, div.post-content")
        if desc_el and isinstance(desc_el, Tag):
            key_dates_heading = desc_el.find(lambda tag: isinstance(tag, Tag) and tag.name.startswith('h') and "key dates" in tag.get_text(strip=True).lower())
            if key_dates_heading:
                element_container = key_dates_heading.find_next_sibling(['ul', 'div']) or key_dates_heading.parent
                if element_container and isinstance(element_container, Tag):
                    closing_date_labels = ["closes", "close", "due", "deadline", "end"]
                    open_date_labels = ["open", "begins", "launches"]
                    for item in element_container.find_all(['li', 'p']):
                        item_text = item.get_text(strip=True)
                        item_text_lower = item_text.lower()
                        if not closing_date_obj and any(label in item_text_lower for label in closing_date_labels):
                            closing_date_obj = _parse_date_from_nsin_line(item_text)
                        if not open_date_obj and any(label in item_text_lower for label in open_date_labels):
                            open_date_obj = _parse_date_from_nsin_line(item_text)
                key_dates_heading.decompose()
                if element_container and isinstance(element_container, Tag) and element_container.name in ['ul', 'div']:
                       element_container.decompose()
            
            for unwanted in desc_el.select('div.social-share-group'):
                if unwanted: unwanted.decompose()
            
            description = ' '.join(desc_el.get_text(separator=" ", strip=True).split())[:3500]

        elif psoup.body:
            for tag_to_remove in psoup(['script', 'style', 'header', 'footer', 'nav', 'aside']):
                tag_to_remove.decompose()
            description = ' '.join(psoup.body.get_text(separator=" ", strip=True).split())[:3500]
            
    except requests.exceptions.RequestException as e_page:
        logger.warning(f"[{module_name}] RequestException for detail page {detail_url}: {e_page}")
    except Exception as e_detail_parse:
        logger.error(f"[{module_name}] Error parsing detail page {detail_url}: {e_detail_parse}", exc_info=False)
        
    return title, description, open_date_obj, closing_date_obj


def fetch_nsin_opportunities(driver: WebDriver, headers: dict):
    """
    Main function to scrape NSIN opportunities.
    This version scrapes all opportunities and filters out those with past closing dates.
    """
    module_name = "NSIN Events"
    logger.info(f"[{module_name}] 🔍 Scraping NSIN events using Selenium...")
    base_url = "https://www.nsin.mil/events/"
    results = []
    
    if not driver:
        logger.error(f"[{module_name}] No WebDriver instance provided. Cannot proceed.")
        return []

    try:
        driver.get(base_url)
        
        card_container_selector = "div.posts-grid__container"
        WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.CSS_SELECTOR, card_container_selector)))
        time.sleep(3)
        
        page_soup = BeautifulSoup(driver.page_source, "html.parser")
        
        container = page_soup.select_one(card_container_selector)
        if not (container and isinstance(container, Tag)):
            logger.warning(f"[{module_name}] Main container '{card_container_selector}' not found.")
            return results

        if "no events found" in container.get_text(strip=True).lower():
            logger.info(f"[{module_name}] Site indicates no events are currently available.")
            return results

        event_card_selector = "article.post--color-event"
        event_cards = container.select(event_card_selector)
        logger.info(f"[{module_name}] Found {len(event_cards)} potential event cards.")

        if not event_cards:
            logger.warning(f"[{module_name}] No event items found with selector. Page structure might have changed.")
            return results

        links_to_visit = []
        for card in event_cards:
            if isinstance(card, Tag):
                link_tag = card.select_one("h3.post__title a")
                if link_tag and isinstance(link_tag, Tag) and link_tag.has_attr('href'):
                    href_val = str(link_tag['href'])
                    full_url = urljoin(base_url, href_val)
                    if full_url not in [item['url'] for item in links_to_visit]:
                        links_to_visit.append({'url': full_url, 'listing_title': link_tag.get_text(strip=True)})

        for link_info in links_to_visit:
            try:
                detail_title, detail_description, open_date, closing_date = _parse_nsin_detail_page(link_info['url'], headers)

                final_title = detail_title if detail_title != "Title Not Found" else link_info['listing_title']
                
                if closing_date and closing_date < datetime.now():
                    logger.info(f"[{module_name}] Skipping '{final_title[:60]}' as its closing date {closing_date.strftime('%Y-%m-%d')} has passed.")
                    continue
                
                logger.info(f"[{module_name}] ✅ Scraping '{final_title[:60]}'")
                results.append({
                    "Source": "NSIN", "Title": final_title, "Description": detail_description, "URL": link_info['url'],
                    "ScrapedDate": datetime.now().isoformat(),
                    "Open Date": open_date.strftime("%Y-%m-%d") if open_date else "N/A",
                    "Close Date": closing_date.strftime("%Y-%m-%d") if closing_date else "N/A"
                })
                
                time.sleep(0.5)

            except Exception as e_detail:
                logger.error(f"[{module_name}] Error processing detail page {link_info['url']}: {e_detail}", exc_info=False)

    except Exception as e_main:
        logger.error(f"[{module_name}] ❌ Main scrape failed: {e_main}", exc_info=True)
    
    logger.info(f"[{module_name}] Finished scraping. Found {len(results)} total opportunities to be analyzed.")
    return results

def _standalone_nsin_create_driver(headers_to_use: dict, headless_mode: bool = True):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    options = Options()
    if headless_mode: options.add_argument('--headless=new')
    options.add_argument(f'user-agent={headers_to_use["User-Agent"]}')
    driver = webdriver.Chrome(options=options)
    return driver

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', force=True)
    
    logger.info(f"🚀 Running {__file__} standalone for testing...")
    
    is_headless_test = True
    standalone_driver = _standalone_nsin_create_driver(MODULE_DEFAULT_HEADERS, headless_mode=is_headless_test)

    if standalone_driver:
        try:
            # Call the updated function without keyword arguments
            test_opportunities = fetch_nsin_opportunities(
                driver=standalone_driver,
                headers=MODULE_DEFAULT_HEADERS
            )

            if test_opportunities:
                print(f"\n--- Scraped {len(test_opportunities)} NSIN Opportunities ---")
                print(json.dumps(test_opportunities, indent=2))
            else:
                print("\nNo NSIN opportunities were found.")
        finally:
            logger.info("Standalone test finished.")
            standalone_driver.quit()
    else:
        logger.error("Failed to create driver for NSIN standalone test.")

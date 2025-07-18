# mtec_module.py

import time
import logging
import json
from bs4 import BeautifulSoup, Tag
from datetime import datetime
import re
import requests
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

MODULE_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
}

def _parse_mtec_date(date_str: str) -> str:
    """Parses a date string and returns it in YYYY-MM-DD format."""
    if not date_str or not date_str.strip() or date_str.lower() in ['n/a', 'tbd']:
        return "N/A"
    
    date_match = re.search(r"((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},\s*\d{4}|\d{1,2}/\d{1,2}/\d{2,4})", date_str, re.IGNORECASE)
    if not date_match:
        logger.warning(f"[MTEC Date Parser] No standard date pattern found in '{date_str}'.")
        return date_str

    date_to_parse = date_match.group(1).replace(',', '')
    for fmt in ("%B %d %Y", "%b %d %Y", "%m/%d/%Y", "%m/%d/%y"):
        try:
            dt_obj = datetime.strptime(date_to_parse, fmt)
            if '%y' in fmt and dt_obj.year > datetime.now().year + 20:
                dt_obj = dt_obj.replace(year=dt_obj.year - 100)
            return dt_obj.strftime("%Y-%m-%d")
        except ValueError:
            continue
            
    logger.warning(f"[MTEC Date Parser] Could not parse extracted date string '{date_to_parse}' from original '{date_str}'.")
    return date_to_parse

def _get_details_from_page(detail_url: str, headers: dict) -> tuple:
    """Fetches and parses the detail page for an MTEC opportunity."""
    title, description, open_date, close_date = "Title Not Found", "Description Not Found", "N/A", "N/A"
    try:
        page_response = requests.get(detail_url, headers=headers, timeout=25)
        page_response.raise_for_status()
        psoup = BeautifulSoup(page_response.text, "html.parser")

        title_el = psoup.select_one("h1.entry-title, h1.page-title, h1.title")
        title = title_el.get_text(strip=True) if title_el else (psoup.title.string.strip() if psoup.title and psoup.title.string else "Title Not Found")

        desc_el = psoup.select_one("div.entry-content, article.content, main#main, div.post-content")
        if desc_el and isinstance(desc_el, Tag):
            key_dates_heading = desc_el.find(lambda tag: isinstance(tag, Tag) and tag.name is not None and tag.name.startswith('h') and "key dates" in tag.get_text(strip=True).lower())
            if key_dates_heading:
                element_container = key_dates_heading.find_next_sibling(['ul', 'div', 'table']) or key_dates_heading.parent
                if element_container and isinstance(element_container, Tag):
                    closing_date_labels = ["responses due", "proposals due", "applications close", "proposals close", "closing date", "submission deadline", "closes"]
                    open_date_labels = ["issue date", "release date", "open", "begins", "launches"]
                    
                    for item_tag in element_container.find_all(['li', 'p', 'tr']):
                        item_text = item_tag.get_text(strip=True)
                        item_text_lower = item_text.lower()
                        if close_date == "N/A" and any(label in item_text_lower for label in closing_date_labels):
                            close_date = _parse_mtec_date(item_text)
                        if open_date == "N/A" and any(label in item_text_lower for label in open_date_labels):
                            open_date = _parse_mtec_date(item_text)
                if key_dates_heading: key_dates_heading.decompose()
                if element_container: element_container.decompose()

            for unwanted in desc_el.select('div.social-share-group'):
                if unwanted: unwanted.decompose()
            description = ' '.join(desc_el.get_text(separator=" ", strip=True).split())[:3500]

        elif psoup.body:
            for tag_to_remove in psoup(['script', 'style', 'header', 'footer', 'nav', 'aside']): tag_to_remove.decompose()
            description = ' '.join(psoup.body.get_text(separator=" ", strip=True).split())[:3500]
            
    except requests.exceptions.RequestException as e_page:
        logger.warning(f"[MTEC Detail] RequestException for {detail_url}: {e_page}")
    except Exception as e_detail_parse:
        logger.error(f"[MTEC Detail] Error parsing {detail_url}: {e_detail_parse}", exc_info=False)
        
    return title, description, open_date, close_date


def fetch_mtec_opportunities(driver: WebDriver, headers: dict) -> list:
    """
    Scrapes for MTEC opportunities using a Google Search strategy.
    This version scrapes all opportunities and filters out those with past closing dates.
    """
    module_name = "MTEC"
    logger.info(f"[{module_name}] 🔍 Using Google Search strategy for MTEC via Selenium...")
    results = []
    
    if not driver:
        logger.error(f"[{module_name}] No WebDriver instance provided.")
        return []

    search_query = 'site:mtec-sc.org/solicitation "Request for Project Proposals"'
    google_url = f"https://www.google.com/search?q={search_query.replace(' ', '+')}"

    try:
        driver.get(google_url)
        
        try:
            accept_button_xpath = "//button[.//div[text()='Accept all']]"
            logger.info(f"[{module_name}] Checking for Google consent button...")
            accept_button = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, accept_button_xpath)))
            accept_button.click()
            logger.info(f"[{module_name}] Google consent button clicked. Pausing...")
            time.sleep(3)
        except TimeoutException:
            logger.info(f"[{module_name}] Google consent button not found. Proceeding.")
        except Exception as e_consent:
            logger.warning(f"[{module_name}] Could not click Google consent button: {e_consent}")

        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.ID, "result-stats")))
        
        soup = BeautifulSoup(driver.page_source, "html.parser")
        
        search_results = soup.select("div.g") 
        logger.info(f"[{module_name}] Found {len(search_results)} potential results from Google.")

        for result_div in search_results:
            try:
                link_tag = result_div.select_one("a[href]")
                href_value = str(link_tag.get('href')) if link_tag else ''
                if not href_value.startswith("https://www.mtec-sc.org/solicitation/"):
                    continue

                url = href_value
                
                title, description, open_date, close_date = _get_details_from_page(url, headers)

                if close_date and close_date != "N/A" and len(close_date) == 10:
                    try:
                        if datetime.strptime(close_date, "%Y-%m-%d") < datetime.now():
                            logger.info(f"[{module_name}] Skipping '{title[:60]}' as its closing date {close_date} has passed.")
                            continue
                    except ValueError:
                        pass
                
                logger.info(f"✅ [{module_name}] Scraping '{title[:60]}'")
                results.append({
                    "Source": "MTEC", "Title": title, "Description": description, "URL": url,
                    "ScrapedDate": datetime.now().isoformat(),
                    "Open Date": open_date if open_date else "N/A",
                    "Close Date": close_date if close_date else "N/A"
                })

                time.sleep(1)

            except Exception as e_item:
                logger.error(f"[{module_name}] Error processing Google search result item: {e_item}", exc_info=False)

    except Exception as e_main:
        logger.error(f"[{module_name}] Main Google Search scrape failed for MTEC: {e_main}", exc_info=True)

    logger.info(f"[{module_name}] Finished scraping MTEC. Found {len(results)} total opportunities to be analyzed.")
    return results

def _standalone_mtec_create_driver(headless_mode=True):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    options = Options()
    if headless_mode: options.add_argument('--headless=new')
    options.add_argument(f'user-agent={MODULE_DEFAULT_HEADERS["User-Agent"]}')
    driver = webdriver.Chrome(options=options)
    return driver

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(funcName)s - %(message)s')
    logger.info(f"🚀 Running {__name__} standalone for MTEC testing...")

    standalone_driver_mtec = _standalone_mtec_create_driver(headless_mode=False)

    if standalone_driver_mtec:
        try:
            test_opportunities = fetch_mtec_opportunities(
                driver=standalone_driver_mtec,
                headers=MODULE_DEFAULT_HEADERS
            )
            if test_opportunities:
                logger.info(f"\n--- Scraped {len(test_opportunities)} MTEC Opportunities (Standalone Test) ---")
                print(json.dumps(test_opportunities, indent=2))
            else:
                logger.info("No MTEC opportunities found during standalone test.")
        finally:
            standalone_driver_mtec.quit()
    else:
        logger.error("Failed to create driver for MTEC standalone test.")
    logger.info(f"🏁 Standalone test for {__name__} finished.")

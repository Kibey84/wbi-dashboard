# grantsgov_module.py

import time
import logging
from bs4 import BeautifulSoup, Tag
from urllib.parse import urljoin
from datetime import datetime
import json
from typing import Optional

# Selenium Imports
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.remote.webdriver import WebDriver

# Setup logger
logger = logging.getLogger(__name__)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(funcName)s - %(message)s')
    _handler.setFormatter(_formatter)
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

MODULE_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
}

def _parse_grants_date(date_str: Optional[str]) -> str:
    """Parses date string from Grants.gov and returns in YYYY-MM-DD format."""
    if not date_str or not date_str.strip(): return "N/A"
    try:
        dt_obj = datetime.strptime(date_str.strip(), "%m/%d/%Y")
        return dt_obj.strftime("%Y-%m-%d")
    except ValueError:
        logger.warning(f"[Grants.gov] Could not parse date: {date_str}")
        return date_str

def fetch_grantsgov_opportunities(driver: WebDriver) -> list:
    """
    Scrapes the 'simpler' Grants.gov search page for all 'posted' opportunities.
    This version scrapes all opportunities without keyword filtering.
    """
    module_name = "Grants.gov (Simpler)"
    search_url = "https://simpler.grants.gov/search/"
    base_url = "https://www.grants.gov"
    results = []
    max_pages_to_load = 5

    logger.info(f"[{module_name}] 🔍 Scraping from {search_url}...")
    
    if not driver:
        logger.error(f"[{module_name}] No WebDriver instance provided.")
        return []

    try:
        driver.get(search_url)
        
        load_more_selector = "button.usa-button"
        results_list_selector = "ul.usa-list"
        
        WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.CSS_SELECTOR, results_list_selector)))
        
        for i in range(max_pages_to_load):
            logger.info(f"[{module_name}] Clicking 'Load more'... (Attempt {i + 1}/{max_pages_to_load})")
            try:
                load_more_button = driver.find_element(By.CSS_SELECTOR, load_more_selector)
                driver.execute_script("arguments[0].scrollIntoView(true);", load_more_button)
                time.sleep(1)
                load_more_button.click()
                time.sleep(3)
            except Exception:
                logger.info(f"[{module_name}] No more 'Load more' button found or it failed to click. Processing results found so far.")
                break
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        list_items = soup.select(f"{results_list_selector} > li")

        logger.info(f"[{module_name}] Found {len(list_items)} total opportunities to process.")

        for item in list_items:
            try:
                title_tag = item.select_one("h3 a")
                if not title_tag: continue
                
                title = title_tag.get_text(strip=True)
                detail_url = urljoin(base_url, str(title_tag.get('href')))

                meta_items = item.select("div.g-meta > div")
                meta_dict = {}
                for meta in meta_items:
                    label = meta.select_one("span.g-label")
                    value = meta.select_one("span.g-value")
                    if label and value:
                        meta_dict[label.get_text(strip=True).lower()] = value.get_text(strip=True)

                status = meta_dict.get('status', '').lower()

                if "posted" not in status:
                    continue

                opp_number = meta_dict.get('opportunity #', '')
                agency = meta_dict.get('agency', '')
                close_date_str = _parse_grants_date(meta_dict.get('close date'))
                open_date_str = _parse_grants_date(meta_dict.get('post date'))

                logger.info(f"✅ [{module_name}] Scraping Opp#: {opp_number} - '{title[:60]}'")
                results.append({
                    "Source": "Grants.gov",
                    "Title": title,
                    "Description": f"Agency: {agency} | Opportunity Number: {opp_number}",
                    "URL": detail_url,
                    "ScrapedDate": datetime.now().isoformat(),
                    "Open Date": open_date_str,
                    "Close Date": close_date_str
                })
            except Exception as e_row:
                logger.error(f"[{module_name}] Error processing one list item: {e_row}")

    except Exception as e:
        logger.error(f"[{module_name}] A critical error occurred: {e}", exc_info=True)

    logger.info(f"[{module_name}] Finished. Found {len(results)} total 'posted' opportunities to be analyzed.")
    return results

def _standalone_grantsgov_create_driver(headless_mode=True):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    options = Options()
    if headless_mode: options.add_argument('--headless=new')
    options.add_argument(f'user-agent={MODULE_DEFAULT_HEADERS["User-Agent"]}')
    driver = webdriver.Chrome(options=options)
    return driver

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(funcName)s - %(message)s')
    logger.info(f"🚀 Running {__name__} standalone for Grants.gov testing...")

    is_headless_test = False
    standalone_driver_grants = _standalone_grantsgov_create_driver(headless_mode=is_headless_test)

    if standalone_driver_grants:
        try:
            test_opportunities = fetch_grantsgov_opportunities(driver=standalone_driver_grants)
            if test_opportunities:
                logger.info(f"\n--- Scraped {len(test_opportunities)} Grants.gov Opportunities (Standalone Test) ---")
                print(json.dumps(test_opportunities, indent=2))
            else:
                logger.info("No 'posted' Grants.gov opportunities found during standalone test.")
        finally:
            time.sleep(5)
            standalone_driver_grants.quit()
    else:
        logger.error("Failed to create driver for Grants.gov standalone test.")
    logger.info(f"🏁 Standalone test for {__name__} finished.")

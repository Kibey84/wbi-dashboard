# nih_sbir_module.py
import time
import logging
from urllib.parse import urljoin
from datetime import datetime
import re
import json
from typing import Optional

# Selenium Imports
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.remote.webdriver import WebDriver

from bs4 import BeautifulSoup, Tag
# FIX: Correctly import NavigableString from its specific submodule
from bs4.element import NavigableString

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

def _extract_nih_field(soup: BeautifulSoup, label_text_variations: list) -> Optional[str]:
    """Helper to find data associated with a label in the NIH page structure."""
    for label_text in label_text_variations:
        def is_potential_label(tag: Tag):
            if not isinstance(tag, Tag) or tag.name not in ['div', 'td', 'dt', 'p', 'span', 'strong', 'b']:
                return False
            tag_text = tag.get_text(strip=True, separator=' ').lower()
            if len(tag_text) > 200: return False
            return label_text.lower().strip(':') in tag_text

        label_el = soup.find(is_potential_label)
        if label_el and isinstance(label_el, Tag):
            if label_el.parent and isinstance(label_el.parent, Tag):
                if label_el.name in ['strong', 'b'] and label_el.next_sibling and isinstance(label_el.next_sibling, NavigableString):
                    value_candidate = str(label_el.next_sibling).strip(": ")
                    if value_candidate: return value_candidate

            value_el = label_el.find_next_sibling(['p', 'div', 'td', 'dd'])
            if value_el and isinstance(value_el, Tag):
                value_text = value_el.get_text(strip=True)
                if value_text: return value_text
    return None

def _parse_nih_date(date_str: Optional[str]) -> str:
    """Parses a date string from NIH and returns in YYYY-MM-DD format."""
    if not date_str: return "N/A"
    date_str = date_str.strip()
    
    date_match = re.search(r"(\w+\s+\d{1,2},\s*\d{4})", date_str, re.IGNORECASE)
    if not date_match:
        return date_str

    date_to_parse = date_match.group(1).replace(',', '')
    for fmt in ("%B %d %Y", "%b %d %Y"):
        try:
            dt_obj = datetime.strptime(date_to_parse, fmt)
            return dt_obj.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return date_to_parse

def _process_single_detail_page(driver: WebDriver, link_url: str, listing_title_hint: str):
    """Processes a single NIH opportunity detail page."""
    module_name = "NIH SBIR Detail"
    try:
        driver.get(link_url)
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        sub_soup = BeautifulSoup(driver.page_source, "html.parser")

        final_title = listing_title_hint
        title_el = sub_soup.select_one("h1#opportunity-title, h1.pageheader span")
        if title_el:
            final_title = title_el.get_text(strip=True)
        elif sub_soup.title and sub_soup.title.string:
            final_title = sub_soup.title.string.strip()

        due_date_text = _extract_nih_field(sub_soup, ["Application Due Date(s)", "Expiration Date"])
        application_due_date_str = _parse_nih_date(due_date_text)

        # Skip if the opportunity has already expired
        if application_due_date_str and application_due_date_str != "N/A" and len(application_due_date_str) == 10:
            try:
                if datetime.strptime(application_due_date_str, "%Y-%m-%d") < datetime.now():
                    logger.info(f"[{module_name}] Skipping '{final_title[:60]}' (Expired).")
                    return None
            except (ValueError, TypeError): pass

        desc_el = sub_soup.select_one("div.contentbody, div#opportunityDetailView, #main-content")
        final_desc_cleaned = desc_el.get_text(separator=' ', strip=True) if isinstance(desc_el, Tag) else "Description not found."
        
        logger.info(f"✅ [{module_name}] Scraping '{final_title[:60]}'")
        return {
            "Source": "NIH SBIR", "Title": final_title, 
            "Description": final_desc_cleaned[:3500], "URL": link_url, 
            "ScrapedDate": datetime.now().isoformat(), "Close Date": application_due_date_str
        }
    except Exception as e:
        logger.error(f"[{module_name}] Error processing detail page {link_url}: {e}", exc_info=True)
        return None

def fetch_nih_sbir_opportunities(driver: WebDriver):
    """
    Main function to scrape NIH SBIR/STTR opportunities.
    This version scrapes all opportunities and filters out those with past closing dates.
    """
    module_name = "NIH SBIR"
    site_url = "https://seed.nih.gov/small-business-funding/find-funding/sbir-sttr-funding-opportunities"
    results = []

    if not driver:
        logger.error(f"[{module_name}] No WebDriver instance provided.")
        return []
    logger.info(f"[{module_name}] 🔍 Scraping from {site_url}...")

    try:
        driver.get(site_url)
        WebDriverWait(driver, 30).until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "div.table-responsive table tbody tr")))
        soup = BeautifulSoup(driver.page_source, "html.parser")
        table_body = soup.select_one("div.table-responsive table tbody")
        
        if not (table_body and isinstance(table_body, Tag)):
            logger.warning(f"[{module_name}] Could not find table body.")
            return results

        rows = table_body.find_all("tr")
        links_to_visit = []
        for row in rows:
            if not isinstance(row, Tag): continue
            
            cells = row.find_all('td')
            if len(cells) < 2: continue

            # FIX: Process cells explicitly to help Pylance type inference
            first_cell = cells[0]
            second_cell = cells[1]

            if not (isinstance(first_cell, Tag) and isinstance(second_cell, Tag)):
                continue

            title_el = first_cell.find('a')
            title_text = ""
            if title_el and isinstance(title_el, Tag):
                title_text = title_el.get_text(strip=True)
            else:
                title_text = first_cell.get_text(strip=True)
            
            link_tag = second_cell.find('a')
            if link_tag and isinstance(link_tag, Tag):
                href = str(link_tag.get('href', ''))
                if "grants.nih.gov/grants/guide" in href:
                    links_to_visit.append({'url': urljoin(site_url, href), 'listing_title': title_text})
        
        logger.info(f"[{module_name}] Extracted {len(links_to_visit)} unique links.")

        for idx, link_info in enumerate(links_to_visit):
            logger.info(f"[{module_name}] Processing detail page {idx+1}/{len(links_to_visit)}: {link_info['url']}")
            opp_data = _process_single_detail_page(driver, link_info['url'], link_info['listing_title'])
            if opp_data: results.append(opp_data)
            time.sleep(0.5)

    except Exception as e_main:
        logger.error(f"[{module_name}] Main scrape function failed: {e_main}", exc_info=True)

    logger.info(f"[{module_name}] Finished scraping. Found {len(results)} total opportunities to be analyzed.")
    return results

def _standalone_nih_create_driver(headless_mode=True):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options as ChromeOptionsStandalone
    options = ChromeOptionsStandalone()
    if headless_mode: options.add_argument('--headless=new')
    options.add_argument(f'user-agent={MODULE_DEFAULT_HEADERS["User-Agent"]}')
    driver = webdriver.Chrome(options=options)
    return driver

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(funcName)s - %(message)s', force=True)

    logger.info(f"🚀 Running {__file__} standalone for testing NIH SBIR module...")
    
    is_headless_test = False 
    standalone_driver_nih = _standalone_nih_create_driver(headless_mode=is_headless_test) 

    if standalone_driver_nih:
        try:
            # Call the updated function without keyword arguments
            scraped_data_nih = fetch_nih_sbir_opportunities(driver=standalone_driver_nih)
            
            if scraped_data_nih:
                print(f"\n--- Scraped {len(scraped_data_nih)} NIH SBIR Opportunities ---")
                for item in scraped_data_nih:
                    print(json.dumps(item, indent=2))
            else:
                print("\nNo NIH SBIR opportunities found.")
        except Exception as e_test:
            logger.error(f"Error in NIH standalone test: {e_test}", exc_info=True)
        finally:
            logger.info("Standalone test finished. Pausing before closing browser...")
            time.sleep(1 if is_headless_test else 10)
            standalone_driver_nih.quit()
            logger.info("Standalone WebDriver for NIH quit.")
    else:
        logger.error("Failed to create driver for NIH standalone test.")

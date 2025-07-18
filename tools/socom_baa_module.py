# socom_baa_module.py

import logging
import time
from datetime import datetime
from urllib.parse import urljoin
import re
import json
from typing import Optional

# Selenium Imports
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

# BeautifulSoup Imports
from bs4 import BeautifulSoup, Tag

# --- Module-level logger setup ---
module_logger = logging.getLogger(__name__)
if not module_logger.handlers:
    _handler = logging.StreamHandler()
    _formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(funcName)s - %(message)s')
    _handler.setFormatter(_formatter)
    module_logger.addHandler(_handler)
    module_logger.setLevel(logging.INFO)
    module_logger.propagate = False

# --- Module Configuration ---
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 OpportunityScraperModule/1.2"
}

def fetch_socom_opportunities(driver: WebDriver, max_items_from_wbiops: Optional[int] = None) -> list:
    """
    Scrapes the SOCOM BAA page for all new opportunities using Selenium.
    This version scrapes all opportunities without keyword filtering.
    """
    target_url = "https://www.socom.mil/SOF-ATL/Pages/baa.aspx"
    base_url = "https://www.socom.mil"
    module_logger.info(f"Starting scrape of {target_url}")

    scraped_opportunities = []

    try:
        driver.get(target_url)
        wait_selector = "table.ms-listviewtable tr.ms-itmHoverEnabled"
        module_logger.info(f"Waiting for table rows ('{wait_selector}') to be rendered...")
        
        WebDriverWait(driver, 30).until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, wait_selector)))
        module_logger.info("Table rows appear to be rendered.")
        time.sleep(2) 

        soup = BeautifulSoup(driver.page_source, "html.parser")
        
        baa_table = soup.find('table', summary="BAA, RFI, & CSO Announcement Board")
        if not (baa_table and isinstance(baa_table, Tag)):
            module_logger.warning("Could not find table by summary. Falling back to class search.")
            baa_table = soup.find('table', class_="ms-listviewtable")
        
        if not (baa_table and isinstance(baa_table, Tag)):
            module_logger.error("The main BAA announcement table was not found.")
            return []

        rows = baa_table.select('tbody tr.ms-itmHoverEnabled')
        module_logger.info(f"Found {len(rows)} opportunity rows in the table.")

        for row in rows:
            if max_items_from_wbiops is not None and len(scraped_opportunities) >= max_items_from_wbiops:
                module_logger.info(f"Reached max items limit ({max_items_from_wbiops}).")
                break
            
            if not isinstance(row, Tag): continue

            cells = row.find_all('td', class_='ms-cellstyle')
            if len(cells) < 4:
                module_logger.warning(f"Row has {len(cells)} cells, expected at least 4. Skipping.")
                continue

            opportunity_title = cells[0].get_text(strip=True)
            
            link_cell = cells[1]
            if not isinstance(link_cell, Tag): continue
            link_tag = link_cell.find('a', href=True)

            if not link_tag or not isinstance(link_tag, Tag):
                module_logger.debug(f"No valid link tag found for '{opportunity_title}'. Skipping.")
                continue

            href = str(link_tag.get('href', ""))
            if any(term in href.lower() for term in ["_archive/", "sub-form.aspx", "template.", "instructions"]):
                continue
            
            opportunity_url = urljoin(base_url, href)

            # --- Date Parsing ---
            start_date_raw = cells[2].get_text(strip=True)
            end_date_raw = cells[3].get_text(strip=True)
            
            formatted_close_date = "N/A"
            if end_date_raw:
                date_match = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", end_date_raw)
                if date_match:
                    try:
                        dt_object = datetime.strptime(date_match.group(1), '%m/%d/%Y')
                        formatted_close_date = dt_object.strftime('%Y-%m-%d')
                    except ValueError:
                        formatted_close_date = end_date_raw
                else:
                    formatted_close_date = end_date_raw
            
            description = f"Details for '{opportunity_title}' posted on SOCOM BAA page. Original Close Date: {end_date_raw}."

            module_logger.info(f"Scraping '{opportunity_title}'")
            scraped_opportunities.append({
                "Source": "SOCOM BAA",
                "Title": opportunity_title,
                "Description": description,
                "URL": opportunity_url,
                "Close Date": formatted_close_date,
                "ScrapedDate": datetime.now().isoformat()
            })

    except TimeoutException as e_timeout:
        module_logger.error(f"Timeout waiting for page elements: {e_timeout}", exc_info=False)
    except Exception as e:
        module_logger.error(f"An unexpected error occurred: {e}", exc_info=True)

    module_logger.info(f"Finished. Scraped {len(scraped_opportunities)} total opportunities to be analyzed.")
    return scraped_opportunities

if __name__ == "__main__":
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options as ChromeOptionsStandalone
    
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(name)s - %(funcName)s - %(message)s')
    
    test_driver = None
    try:
        options = ChromeOptionsStandalone()
        options.add_argument(f"user-agent={DEFAULT_HEADERS['User-Agent']}")
        test_driver = webdriver.Chrome(options=options)

        opportunities = fetch_socom_opportunities(
            driver=test_driver,
            max_items_from_wbiops=5
        )
        
        if opportunities:
            print(f"\n--- Found {len(opportunities)} SOCOM BAA Opportunities ---")
            print(json.dumps(opportunities, indent=2))
        else:
            print("No SOCOM BAA opportunities found.")

    except Exception as e_standalone:
        module_logger.error(f"Error during standalone test: {e_standalone}", exc_info=True)
    finally:
        if test_driver:
            module_logger.info("Closing test browser...")
            test_driver.quit()

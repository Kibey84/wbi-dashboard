# nstxl_module.py

import time
import logging
from bs4 import BeautifulSoup, Tag
from urllib.parse import urljoin
from datetime import datetime
import re
import json

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

# --- Module Configuration ---
MODULE_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
}

def _parse_nstxl_date(date_text_str: str, module_name_for_log="NSTXL") -> str:
    """
    Parses various date string formats and returns a standardized 'YYYY-MM-DD' string or 'N/A'.
    """
    if not date_text_str or not date_text_str.strip():
        return "N/A"

    cleaned_date_text = date_text_str.strip()
    labels_to_strip = ["proposals due", "responses due", "closing date", "submission deadline", "applications due", "deadline", "date offers due", "offers due"]
    for label in labels_to_strip:
        cleaned_date_text = re.sub(rf"^\s*{re.escape(label)}\s*[:\-]?\s*", "", cleaned_date_text, count=1, flags=re.IGNORECASE).strip()

    date_pattern = r"((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}(?:st|nd|rd|th)?(?:,)?\s*\d{4}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\d{4}-\d{2}-\d{2})"
    match = re.search(date_pattern, cleaned_date_text, re.IGNORECASE)

    if not match:
        if any(term in cleaned_date_text.lower() for term in ["n/a", "tbd"]):
            return "N/A"
        logger.warning(f"[{module_name_for_log}] No standard date pattern found in: '{date_text_str}'")
        return "N/A (Unrecognized)"

    extracted_date_str = match.group(1)
    string_to_parse = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", extracted_date_str, flags=re.IGNORECASE)

    for fmt in ("%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y", "%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%m.%d.%Y", "%m/%d/%y", "%m-%d-%y", "%m.%d.%y"):
        try:
            dt_obj = datetime.strptime(string_to_parse, fmt)
            return dt_obj.strftime("%Y-%m-%d")
        except ValueError:
            continue
    
    logger.error(f"[{module_name_for_log}] Failed to parse extracted date string: '{string_to_parse}'")
    return "N/A (Parsing Failed)"

def fetch_nstxl_opportunities(driver: WebDriver) -> list:
    """
    Main function to scrape the NSTXL opportunities listing page.
    This version scrapes all opportunities and filters out those with past closing dates.
    """
    module_name = "NSTXL"
    site_url = "https://nstxl.org/opportunities/"
    results = []
    
    if not driver:
        logger.error(f"[{module_name}] No WebDriver instance provided.")
        return []

    logger.info(f"[{module_name}] 🔍 Scraping from {site_url}...")

    try:
        driver.get(site_url)
        try:
            cookie_button = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button#cn-accept-cookie, button[data-cky-tag='accept-button']"))
            )
            cookie_button.click()
            logger.info(f"[{module_name}] Clicked cookie consent button.")
            time.sleep(1)
        except TimeoutException:
            logger.info(f"[{module_name}] No cookie consent pop-up found or it was not clickable.")
        
        wait_for_selector = "h2.entry-title.fusion-post-title a[href]"
        WebDriverWait(driver, 30).until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, wait_for_selector)))

        link_elements = driver.find_elements(By.CSS_SELECTOR, "h2.entry-title.fusion-post-title > a[href]")
        links_to_visit = [{'url': el.get_attribute('href'), 'listing_title': el.text.strip()} for el in link_elements if el.get_attribute('href')]
        logger.info(f"[{module_name}] Found {len(links_to_visit)} unique links.")

        for idx, link_info in enumerate(links_to_visit[:25]): # Limit to first 25 to be efficient
            link_url = link_info['url']
            logger.info(f"[{module_name}] Processing page {idx+1}/{len(links_to_visit)}: {link_url}")
            try:
                driver.get(link_url)
                WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, "article.post, main#main, div.post-content")))
                time.sleep(2)
                sub_soup = BeautifulSoup(driver.page_source, "html.parser")

                title_el = sub_soup.select_one("h1.entry-title, h1.fusion-post-title")
                final_title = title_el.get_text(strip=True) if title_el else link_info['listing_title']

                desc_el = sub_soup.select_one("div.post-content, div.entry-content")
                if desc_el and isinstance(desc_el, Tag):
                    for unwanted_selector in ['script', 'style', 'form', 'button', '.related-posts']:
                        for tag in desc_el.select(unwanted_selector):
                            tag.decompose()
                    final_desc_cleaned = ' '.join(desc_el.get_text(separator=" ", strip=True).split())[:3500]
                else:
                    final_desc_cleaned = "Description could not be extracted."

                close_date_str = "N/A"
                date_text_candidates = sub_soup.find_all(string=re.compile(r"(proposals|responses|offers)\s+due|closing\s+date|submission\s+deadline", re.I))
                if date_text_candidates:
                    parent_element = date_text_candidates[0].find_parent(['p', 'h1', 'h2', 'h3', 'h4', 'div'])
                    if parent_element:
                        close_date_str = _parse_nstxl_date(parent_element.get_text(), module_name)
                
                if close_date_str.startswith("N/A"):
                    h6_date_el = sub_soup.select_one("h6.fusion-title-heading")
                    if h6_date_el:
                         close_date_str = _parse_nstxl_date(h6_date_el.get_text(), module_name)

                if not close_date_str.startswith("N/A"):
                    try:
                        close_date_obj = datetime.strptime(close_date_str, "%Y-%m-%d")
                        if close_date_obj < datetime.now():
                            logger.info(f"[{module_name}] SKIPPING (Past Date) - '{final_title[:60]}'")
                            continue
                    except ValueError:
                        logger.warning(f"[{module_name}] Could not parse date '{close_date_str}' for filtering.")
                
                logger.info(f"[{module_name}] ✅ Scraping '{final_title[:60]}'")
                results.append({
                    "Source": module_name, "Title": final_title, "Description": final_desc_cleaned,
                    "URL": link_url, "Close Date": close_date_str,
                    "ScrapedDate": datetime.now().isoformat()
                })

            except Exception as e_subpage:
                logger.error(f"[{module_name}] Error processing subpage {link_url}: {e_subpage}", exc_info=True)

    except Exception as e_main:
        logger.error(f"[{module_name}] Main scrape failed: {e_main}", exc_info=True)

    logger.info(f"[{module_name}] Finished. Found {len(results)} total opportunities to be analyzed.")
    return results

# --- Standalone Testing ---
def _standalone_nstxl_create_driver(headless_mode=True):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    options = Options()
    if headless_mode: options.add_argument('--headless=new')
    options.add_argument(f'user-agent={MODULE_DEFAULT_HEADERS["User-Agent"]}')
    driver = webdriver.Chrome(options=options)
    return driver

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', force=True)
    logger.info(f"🚀 Running {__file__} standalone for NSTXL testing...")

    standalone_driver_nstxl = _standalone_nstxl_create_driver(headless_mode=False)

    if standalone_driver_nstxl:
        try:
            test_opportunities = fetch_nstxl_opportunities(driver=standalone_driver_nstxl)
            if test_opportunities:
                print("\n--- Scraped NSTXL Opportunities ---")
                print(json.dumps(test_opportunities, indent=2))
            else:
                print("\nNo matching NSTXL opportunities were found.")
        except Exception as e_test:
            logger.error(f"Error in NSTXL standalone test: {e_test}", exc_info=True)
        finally:
            logger.info("Standalone test finished. Pausing for 10 seconds...")
            time.sleep(10)
            standalone_driver_nstxl.quit()
    else:
        logger.error("Failed to create driver for NSTXL standalone test.")

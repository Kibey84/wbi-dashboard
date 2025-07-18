# arpah_module.py

# ====== IMPORTS ======
import time
import logging
from urllib.parse import urljoin
from datetime import datetime 
import re
import json

# Selenium imports
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, JavascriptException
from selenium.webdriver.remote.webdriver import WebDriver 
from selenium.webdriver.remote.webelement import WebElement 

from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag

logger = logging.getLogger(__name__)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(funcName)s - %(message)s')
    _handler.setFormatter(_formatter)
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO) 
    logger.propagate = False


DEFAULT_HEADERS_ARPAH = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
}

def parse_date_from_text(date_text_str: str, site_name_for_log="ARPA-H Detail") -> str:
    """Parses various date string formats and returns a standardized 'YYYY-MM-DD' string or 'N/A'."""
    if not date_text_str or not date_text_str.strip() or date_text_str.lower() == 'n/a':
        return "N/A"

    cleaned_date_text = date_text_str.strip()
    labels_to_strip = [
        "submission deadline", "offers due", "date offers due", "closing date", 
        "response date", "updated date offers due", "expiration date", 
        "proposal due date", "application due date", "deadline", 
        "proposers’ day", "proposers day", "proposer's day"
    ]
    
    for label in labels_to_strip:
        pattern_label_prefix = rf"^\s*{re.escape(label)}\s*[:\-]?\s*"
        if re.match(pattern_label_prefix, cleaned_date_text, re.IGNORECASE):
            cleaned_date_text = re.sub(pattern_label_prefix, "", cleaned_date_text, count=1, flags=re.IGNORECASE).strip()
            break 
    
    month_names_pattern = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    pat1 = rf"({month_names_pattern}\s+\d{{1,2}}(?:st|nd|rd|th)?\s*,\s*\d{{4}})"
    pat2 = r"(\d{1,2}/\d{1,2}/\d{2,4})"
    pat3 = r"(\d{4}-\d{1,2}-\d{1,2})"
    combined_pattern = f"({pat1}|{pat2}|{pat3})"
    
    match = re.search(combined_pattern, cleaned_date_text, re.IGNORECASE)
    if match:
        date_to_parse = next(g for g in match.groups() if g is not None)
        date_to_parse = re.sub(r"\s*(at|by|before|until|,)?\s*\d{1,2}:\d{2}.*", "", date_to_parse, flags=re.IGNORECASE).strip()
        
        for fmt in ("%B %d, %Y", "%b %d, %Y", "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
            try:
                dt_obj = datetime.strptime(date_to_parse, fmt)
                if '%y' in fmt and dt_obj.year > datetime.now().year + 20 : 
                    dt_obj = dt_obj.replace(year=dt_obj.year - 100)
                return dt_obj.strftime("%Y-%m-%d")
            except ValueError:
                continue
    
    logger.warning(f"[{site_name_for_log}] No standard date pattern was successfully parsed from: '{cleaned_date_text}'")
    return "N/A"

def fetch_arpah_opportunities(driver: WebDriver, headers: dict):
    """
    Scrapes the ARPA-H 'Open Funding Opportunities' page.
    This version scrapes all opportunities without keyword filtering.
    """
    SITE_CONFIG_ARPAH = {
        "name": "ARPA-H",
        "url": "https://arpa-h.gov/explore-funding/open-funding-opportunities/",
        "link_selector_on_main_page": "div.field--name-body p > a[href], div.field--name-body div.fusion-button-wrapper > a[href]",
        "max_links_to_follow": 25 
    }

    site_name = SITE_CONFIG_ARPAH["name"]
    site_url = SITE_CONFIG_ARPAH["url"]
    logger.info(f"[{site_name} Module] Scraping from {site_url}...")
    results = []

    if not driver:
        logger.error(f"[{site_name} Module] No WebDriver instance provided. Cannot proceed.")
        return results

    try:
        driver.get(site_url)
        main_content_div_selector = "div.field--name-body" 
        try:
            WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, main_content_div_selector)))
        except TimeoutException:
            logger.warning(f"[{site_name} Module] Timeout waiting for main content area.")
            return results

        link_elements = driver.find_elements(By.CSS_SELECTOR, SITE_CONFIG_ARPAH["link_selector_on_main_page"])
        links_to_visit = []
        processed_detail_urls = set()

        for el in link_elements:
            href = el.get_attribute("href")
            if href and not href.startswith("javascript:"):
                full_url = urljoin(site_url, href)
                if full_url not in processed_detail_urls:
                    links_to_visit.append({"url": full_url, "text_hint": el.text.strip() if el.text else "N/A"})
                    processed_detail_urls.add(full_url)
        
        logger.info(f"[{site_name} Module] Extracted {len(links_to_visit)} unique links to process.")

        for idx, link_info in enumerate(links_to_visit[:SITE_CONFIG_ARPAH["max_links_to_follow"]]):
            detail_url = link_info["url"]
            logger.info(f"[{site_name} Module] Processing detail page {idx+1}/{len(links_to_visit)}: {detail_url}")
            
            close_date_str_final = "N/A" 
            try:
                driver.get(detail_url)
                WebDriverWait(driver, 25).until(EC.presence_of_element_located((By.CSS_SELECTOR, "main, article, body")))
                time.sleep(1) 
                
                page_soup = BeautifulSoup(driver.page_source, "html.parser")
                
                title_text = link_info["text_hint"]
                desc_content_cleaned = "Description could not be extracted."

                title_el = page_soup.select_one("h1.page-title, h1.title, h1.wp-block-post-title, h1")
                if title_el: title_text = title_el.get_text(strip=True)
                
                desc_el = page_soup.select_one("article .field--name-body, div.entry-content, main#main-content") 
                if desc_el:
                    desc_content_cleaned = ' '.join(desc_el.get_text(separator=" ", strip=True).split())
                
                key_dates_section_header = page_soup.select_one("div.fusion-text h4 > b:-soup-contains('Key Dates'), div.fusion-text h3:-soup-contains('Key Dates')")
                if key_dates_section_header:
                    parent_div_of_key_dates = key_dates_section_header.find_parent("div", class_=lambda c: isinstance(c, str) and "fusion-text" in c.split())
                    if parent_div_of_key_dates:
                        key_dates_lines = parent_div_of_key_dates.get_text(strip=True, separator='\n').splitlines()
                        closing_date_labels = ["closing date", "submission deadline", "proposals due", "deadline", "responses due"]
                        for line in key_dates_lines:
                            if any(label in line.lower() for label in closing_date_labels):
                                close_date_str_final = parse_date_from_text(line, site_name)
                                break 
                
                if close_date_str_final != "N/A":
                    try:
                        if datetime.strptime(close_date_str_final, "%Y-%m-%d") < datetime.now():
                            logger.info(f"[{site_name}] Skipping '{title_text}' (Expired: {close_date_str_final})")
                            continue
                    except ValueError:
                        pass 

                logger.info(f"✅ [{site_name} Module] Scraping '{title_text[:60]}'")
                results.append({
                    "Source": site_name, "Title": title_text, "Description": desc_content_cleaned,
                    "URL": detail_url, "Close Date": close_date_str_final,
                    "ScrapedDate": datetime.now().isoformat()
                })
            except Exception as e_subpage:
                logger.warning(f"[{site_name} Module] Error processing subpage {detail_url}: {e_subpage}", exc_info=False)
            time.sleep(0.5) 

    except Exception as e_main:
        logger.error(f"[{site_name} Module] Main scrape failed: {e_main}", exc_info=True)

    logger.info(f"[{site_name} Module] Finished scraping. Found {len(results)} total opportunities to be analyzed.")
    return results

def _standalone_arpah_create_driver(headless=True):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options as ChromeOptionsStandalone 
    
    options = ChromeOptionsStandalone()
    if headless: options.add_argument('--headless=new')
    options.add_argument(f'user-agent={DEFAULT_HEADERS_ARPAH["User-Agent"]}')
    try:
        driver = webdriver.Chrome(options=options)
        return driver
    except Exception as e_create_driver:
        logger.error(f"Failed to create driver for ARPA-H standalone: {e_create_driver}")
        return None

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(funcName)s - %(message)s')
    
    is_headless_mode = False
    standalone_driver_arpah = _standalone_arpah_create_driver(headless=is_headless_mode) 

    if standalone_driver_arpah:
        try:
            scraped_arpah_data = fetch_arpah_opportunities(driver=standalone_driver_arpah, headers=DEFAULT_HEADERS_ARPAH)
            if scraped_arpah_data:
                print(f"\n--- Scraped {len(scraped_arpah_data)} ARPA-H Opportunities ---")
                print(json.dumps(scraped_arpah_data, indent=2))
            else:
                print("No ARPA-H opportunities found.")
        finally:
            standalone_driver_arpah.quit()
    else:
        print("Failed to create driver for ARPA-H standalone test.")

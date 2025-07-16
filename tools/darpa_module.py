# darpa_module.py
# ====== IMPORTS ======
import time
import logging
from urllib.parse import urljoin
from datetime import datetime
import re
import json

# Selenium Imports
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, ElementClickInterceptedException
from selenium.webdriver.remote.webdriver import WebDriver # For type hinting
from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(funcName)s - %(message)s')
    _handler.setFormatter(_formatter)
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

DEFAULT_HEADERS_DARPA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
}

def parse_darpa_date_from_text(date_text_str: str, module_name_for_log="DARPA Module") -> str:
    """Parses a date string from DARPA-linked sites and returns in YYYY-MM-DD format."""
    if not date_text_str or not date_text_str.strip():
        return "N/A"

    cleaned_date_text = date_text_str.strip()
    labels_to_strip = [
        "original response date", "original date offers due", "updated date offers due", 
        "date offers due", "response date", "expiration date", "closing date", "due date"
    ]
    
    temp_text_for_label_strip = cleaned_date_text.lower()
    for label in labels_to_strip:
        if temp_text_for_label_strip.startswith(label.lower()):
            cleaned_date_text = re.sub(rf"^\s*{re.escape(label)}\s*[:\-]?\s*", "", cleaned_date_text, count=1, flags=re.IGNORECASE).strip()
            break 
    
    month_names = r"(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    date_pattern = rf"({month_names}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,)?\s*\d{{4}}|\d{{1,2}}[-/]\d{{1,2}}[-/]\d{{2,4}}|\d{{4}}-\d{{1,2}}-\d{{1,2}})"
    match = re.search(date_pattern, cleaned_date_text, re.IGNORECASE) 
    
    if match:
        extracted_date_str = match.group(1).strip()
        extracted_date_str = re.sub(r"\s*(at|by|,)?\s*\d{1,2}:\d{2}.*", "", extracted_date_str, flags=re.IGNORECASE).strip()
        
        for fmt in ("%B %d %Y", "%b %d %Y", "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"): 
            try:
                dt_obj = datetime.strptime(extracted_date_str, fmt)
                if '%y' in fmt and dt_obj.year > datetime.now().year + 50:
                     dt_obj = dt_obj.replace(year=dt_obj.year - 100)
                return dt_obj.strftime("%Y-%m-%d")
            except ValueError:
                continue
        return "N/A (Parsing failed)"
    return "N/A (Unrecognized format)"

def fetch_darpa_opportunities(driver: WebDriver, headers: dict):
    """
    Scrapes the DARPA opportunities page, follows links to SAM.gov/Grants.gov,
    and collects all non-expired opportunities.
    """
    DARPA_OPP_URL = "https://www.darpa.mil/work-with-us/opportunities"
    logger.info(f"[DARPA Module] Scraping DARPA from {DARPA_OPP_URL}...")
    results = []
    module_name = "DARPA Module" 

    if not driver:
        logger.error(f"[{module_name}] No WebDriver instance provided. Cannot proceed.")
        return results

    try:
        driver.get(DARPA_OPP_URL)
        logger.info(f"[{module_name}] Page loaded: {driver.current_url}")
        time.sleep(3) 

        show_more_button_xpath = "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'show more')]"
        max_show_more_clicks = 20 
        clicks = 0
        while clicks < max_show_more_clicks:
            try:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(1.5) 
                show_more_button = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, show_more_button_xpath)))
                logger.info(f"[{module_name}] Clicking 'Show More' (Attempt {clicks + 1})...")
                driver.execute_script("arguments[0].click();", show_more_button)
                clicks += 1
                time.sleep(4) 
            except Exception:
                logger.info(f"[{module_name}] 'Show More' button no longer available or clickable after {clicks} clicks.")
                break

        card_selector_on_darpa_page = "div[style*='box-shadow'][class*='bg-white']" 
        sam_gov_links_info = []
        try:
            WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.CSS_SELECTOR, card_selector_on_darpa_page)))
            darpa_page_soup = BeautifulSoup(driver.page_source, "html.parser")
            opportunity_cards = darpa_page_soup.select(card_selector_on_darpa_page)
            logger.info(f"[{module_name}] Found {len(opportunity_cards)} opportunity cards.")

            for card_soup in opportunity_cards:
                sam_link_tag = card_soup.select_one("a[href*='sam.gov'], a[href*='grants.gov']") 
                if sam_link_tag and sam_link_tag.has_attr('href'):
                    href = urljoin(DARPA_OPP_URL, str(sam_link_tag.get('href'))) 
                    h4_title_tag = card_soup.select_one("h4") 
                    darpa_title_hint = h4_title_tag.get_text(strip=True) if h4_title_tag else "Title Hint Not Found"
                    if href not in [item['url'] for item in sam_gov_links_info]:
                        sam_gov_links_info.append({"url": href, "darpa_title_hint": darpa_title_hint})
            logger.info(f"[{module_name}] Extracted {len(sam_gov_links_info)} unique SAM.gov/Grants.gov links.")
        except Exception as e_links:
            logger.error(f"[{module_name}] Error extracting SAM.gov/Grants.gov links: {e_links}", exc_info=True)

        for idx, link_info in enumerate(sam_gov_links_info):
            external_url = link_info["url"]
            darpa_title_hint = link_info["darpa_title_hint"]
            logger.info(f"[{module_name}] Processing external link {idx+1}/{len(sam_gov_links_info)}: {external_url}")
            
            final_title = darpa_title_hint
            final_description = f"Details available at the SAM.gov/Grants.gov URL: {external_url}"
            close_date_str = "See URL"

            try:
                driver.get(external_url)
                WebDriverWait(driver, 60).until(EC.presence_of_element_located((By.CSS_SELECTOR, "section#description, h1.sam-ui-header, #opportunityView")))
                time.sleep(2.5) 

                external_page_soup = BeautifulSoup(driver.page_source, "html.parser")
                
                title_el_external = external_page_soup.select_one("h1[class*='sam-ui-header'], h1#title, #opportunityView h1")
                if title_el_external:
                    final_title = title_el_external.get_text(strip=True)

                desc_container_external = external_page_soup.select_one("section#description, div#description, #synopsisDetails_content_value, div.opportunity-description")
                if desc_container_external:
                    final_description = ' '.join(desc_container_external.get_text(separator=" ", strip=True).split())

                date_li_element = external_page_soup.select_one('li#general-original-response-date')
                if date_li_element:
                    close_date_str = parse_darpa_date_from_text(date_li_element.get_text(strip=True), module_name)
                else:
                    possible_date_sections = external_page_soup.select("ul.usa-unstyled-list li, div[class*='field--label'] + div, dt + dd")
                    for section in possible_date_sections:
                        if any(kw in section.get_text(strip=True, separator=' ').lower() for kw in ["response date", "offers due", "due date", "expiration"]):
                            temp_date = parse_darpa_date_from_text(section.get_text(strip=True), module_name)
                            if "N/A" not in temp_date:
                                close_date_str = temp_date
                                break
                
                # Skip if expired
                if close_date_str and "N/A" not in close_date_str:
                    try:
                        if datetime.strptime(close_date_str, "%Y-%m-%d") < datetime.now():
                            logger.info(f"[{module_name}] Skipping '{final_title}' (Expired: {close_date_str})")
                            continue
                    except ValueError:
                        pass # Date couldn't be parsed, will be included for manual review
                
                logger.info(f"✅ [{module_name}] Scraping '{final_title[:60]}'")
                results.append({
                    "Source": "DARPA", "Title": final_title, "Description": final_description,
                    "URL": external_url, "Close Date": close_date_str, 
                    "ScrapedDate": datetime.now().isoformat()
                })
            except Exception as e_external_page:
                logger.warning(f"[{module_name}] Could not fully process external page {external_url}: {e_external_page}", exc_info=False)
    
    except Exception as e_main_darpa:
        logger.error(f"[{module_name}] Main DARPA scrape failed: {e_main_darpa}", exc_info=True)
    
    logger.info(f"[{module_name}] Finished. Scraped {len(results)} total opportunities to be analyzed.")
    return results

def _standalone_darpa_create_driver(headless=True):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    options = Options()
    if headless: options.add_argument('--headless=new')
    options.add_argument(f'user-agent={DEFAULT_HEADERS_DARPA["User-Agent"]}')
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(120)
    return driver

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(funcName)s - %(message)s')

    is_headless_test_run = False
    standalone_driver_darpa = _standalone_darpa_create_driver(headless=is_headless_test_run) 

    if standalone_driver_darpa:
        try:
            scraped_data_darpa = fetch_darpa_opportunities(
                driver=standalone_driver_darpa,
                headers=DEFAULT_HEADERS_DARPA
            )
            if scraped_data_darpa:
                print(f"\n--- Scraped {len(scraped_data_darpa)} DARPA Opportunities ---")
                print(json.dumps(scraped_data_darpa, indent=2))
            else:
                print("\nNo DARPA opportunities were found during standalone test.")
        finally:
            standalone_driver_darpa.quit()
    else:
        print("Failed to create driver for DARPA standalone test.")

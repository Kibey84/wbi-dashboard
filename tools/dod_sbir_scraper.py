# dod_sbir_scraper.py
# ====== IMPORTS ======
import time
import logging
from urllib.parse import urljoin
from datetime import datetime
import re 
import json
from bs4 import BeautifulSoup, Tag

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, ElementClickInterceptedException
from selenium.webdriver.remote.webdriver import WebDriver 

logger = logging.getLogger(__name__)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(funcName)s - %(message)s')
    _handler.setFormatter(_formatter)
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

DEFAULT_HEADERS_DOD = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
}

def _parse_dod_date(date_text_str_param, module_name_for_log="DoD SBIR Scraper"):
    if not date_text_str_param or not date_text_str_param.strip() or date_text_str_param.lower() == 'n/a':
        return "N/A"

    cleaned_date_text = str(date_text_str_param).strip() 
    labels_to_strip = ["open", "close", "opened", "closed", "pre-release opens", 
                       "submission window closes", "original response date", 
                       "original date offers due", "date offers due"]
    temp_text_for_label_strip = cleaned_date_text.lower()
    for label in labels_to_strip:
        if temp_text_for_label_strip.startswith(label.lower()):
            match_label_prefix = re.match(rf"^\s*{re.escape(label)}\s*[:\-]?\s*", cleaned_date_text, flags=re.IGNORECASE)
            if match_label_prefix:
                cleaned_date_text = cleaned_date_text[match_label_prefix.end():].strip()
                break 
    
    month_names = r"(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    date_pattern_text_month_first = rf"{month_names}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,)?\s*\d{{4}}"
    date_pattern_numeric = r"\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}" 
    date_pattern_iso = r"\d{4}-\d{1,2}-\d{1,2}"
    combined_pattern = f"({date_pattern_text_month_first}|{date_pattern_numeric}|{date_pattern_iso})"
    match = re.search(combined_pattern, cleaned_date_text, re.IGNORECASE) 
    
    if match:
        extracted_date_str = match.group(1).strip()
        # Clean time and timezone info
        extracted_date_str = re.sub(r"\s*(at|by|before|until|,|@)?\s*\d{{1,2}}:\d{{2}}(?::\d{{2}})?\s*(am|pm)?\s*([a-zA-Z]{2,5}T?|[A-Z]{2,5}|[A-Za-z]{2,5}/\s*[A-Za-z]{2,5})?\s*(\(.*\))?$", "", extracted_date_str, flags=re.IGNORECASE).strip()
        extracted_date_str = extracted_date_str.rstrip(',').strip()
        
        # Attempt to parse with various formats
        for fmt in ("%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y", "%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y", "%m-%d-%y"):
            try:
                dt_obj = datetime.strptime(extracted_date_str, fmt)
                # Handle 2-digit years
                if '%y' in fmt.lower() and dt_obj.year > datetime.now().year + 50:
                    dt_obj = dt_obj.replace(year=dt_obj.year - 100)
                return dt_obj.strftime("%Y-%m-%d")
            except ValueError: continue
        
        logger.warning(f"[{module_name_for_log}] Could not parse '{extracted_date_str}' with strptime.")
        return extracted_date_str 
    
    logger.info(f"[{module_name_for_log}] No standard date pattern matched in: '{cleaned_date_text}'")
    return "N/A"


def fetch_dod_sbir_sttr_topics(driver: WebDriver):
    SITE_URL_DOD = "https://www.dodsbirsttr.mil/topics-app/"
    module_name = "DoD SBIR Scraper" 
    logger.info(f"[{module_name}] Scraping {SITE_URL_DOD} for 'Open' topics...")
    results = []

    if not driver:
        logger.error(f"[{module_name}] No WebDriver instance provided. Cannot proceed.")
        return results

    try:
        driver.get(SITE_URL_DOD)
        scrollable_container_selector = "div[infinite-scroll].accordion-padding"
        
        try:
            scrollable_element_to_target = WebDriverWait(driver, 45).until(EC.presence_of_element_located((By.CSS_SELECTOR, scrollable_container_selector)))
            logger.info(f"[{module_name}] Found scrollable container. Scrolling to load all topics...")
            
            last_height = driver.execute_script("return arguments[0].scrollHeight", scrollable_element_to_target)
            consecutive_no_change = 0
            while True:
                driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", scrollable_element_to_target)
                time.sleep(3)
                new_height = driver.execute_script("return arguments[0].scrollHeight", scrollable_element_to_target)
                if new_height == last_height:
                    consecutive_no_change += 1
                    if consecutive_no_change >= 3:
                        logger.info(f"[{module_name}] Reached end of scrollable content.")
                        break
                else:
                    consecutive_no_change = 0
                last_height = new_height
        except TimeoutException:
            logger.error(f"[{module_name}] Could not find or timed out waiting for scrollable container.")
            return []
            
        time.sleep(3) 
        search_context = driver.find_element(By.CSS_SELECTOR, scrollable_container_selector)
        panel_headers = search_context.find_elements(By.XPATH, ".//mat-expansion-panel-header")
        logger.info(f"[{module_name}] Found {len(panel_headers)} total topic panel headers.")

        if not panel_headers:
            logger.error(f"[{module_name}] No panel headers found after scroll attempts.")
            return results
        
        id_pattern_text = r"([A-Z]{1,}\d+[\w\.-]*)" 
        
        for i in range(len(panel_headers)): 
            logger.info(f"--- [{module_name}] Processing Panel Header {i+1} of {len(panel_headers)} ---")
            
            try: 
                current_panel_headers = driver.find_elements(By.XPATH, f"({scrollable_container_selector}//mat-expansion-panel-header)[{i+1}]")
                if not current_panel_headers:
                    logger.warning(f"Panel {i+1} no longer found. Skipping.")
                    continue
                header_to_click = current_panel_headers[0]
                parent_panel_element = header_to_click.find_element(By.XPATH, "./ancestor::mat-expansion-panel")
                
                title_el_selenium = header_to_click.find_element(By.CSS_SELECTOR, "mat-panel-title")
                title_text_raw = title_el_selenium.text.strip()
                
                try: 
                    status_el = title_el_selenium.find_element(By.XPATH, ".//strong[contains(@class, 'topic-status')]")
                    status_text = status_el.text.strip().lower()
                except NoSuchElementException:
                    status_text = "open" if "open" in title_text_raw.lower() else "unknown"
                
                logger.info(f"[{module_name}] Panel {i+1}: Status='{status_text}'")

                if status_text != "open":
                    logger.info(f"[{module_name}] Panel {i+1}: Skipping (not 'open').")
                    continue 
                
                is_expanded = header_to_click.get_attribute("aria-expanded") == "true"
                if not is_expanded:
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", header_to_click)
                    time.sleep(0.5) 
                    header_to_click.click()
                    WebDriverWait(parent_panel_element, 15).until(EC.visibility_of_element_located((By.CSS_SELECTOR, "div.mat-expansion-panel-content")))
                
                content_html = parent_panel_element.find_element(By.CSS_SELECTOR, "div.mat-expansion-panel-content").get_attribute('innerHTML')
                
                # FIX: Check if content_html is not None before parsing
                if content_html:
                    content_soup = BeautifulSoup(content_html, "html.parser")
                    desc_text = content_soup.get_text(separator=' ', strip=True)
                else:
                    logger.warning(f"[{module_name}] Panel {i+1}: Content area was found but its innerHTML was empty.")
                    desc_text = "Description could not be scraped."
                
                id_match = re.match(id_pattern_text, title_text_raw)
                topic_id_text = id_match.group(1) if id_match else f"UNKNOWN_ID_{i+1}"
                
                cleaned_title = re.sub(id_pattern_text, "", title_text_raw, 1).strip(" -:")
                cleaned_title = " ".join(cleaned_title.split()).capitalize()

                full_url = f"{SITE_URL_DOD}#/topic/{topic_id_text}"
                close_date = _parse_dod_date(title_text_raw)

                logger.info(f"✅ [{module_name}] Scraping '{cleaned_title[:70]}...'")
                results.append({
                    "Source": "DoD SBIR/STTR", 
                    "Title": cleaned_title, 
                    "Description": ' '.join(desc_text.split())[:3500], 
                    "URL": full_url, 
                    "Close Date": close_date, 
                    "ScrapedDate": datetime.now().isoformat()
                })
                
            except Exception as e_panel:
                logger.error(f"[{module_name}] Error processing panel {i+1}: {e_panel}", exc_info=True)

    except Exception as e_main:
        logger.error(f"[{module_name}] Main scrape function failed: {e_main}", exc_info=True)
    
    logger.info(f"[{module_name}] Finished scraping. Found {len(results)} 'open' opportunities.")
    return results

def _standalone_dod_create_driver(headless=True):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options as ChromeOptionsStandalone
    options = ChromeOptionsStandalone()
    if headless: options.add_argument('--headless=new')
    options.add_argument(f'user-agent={DEFAULT_HEADERS_DOD["User-Agent"]}')
    try:
        driver = webdriver.Chrome(options=options)
        return driver
    except Exception as e:
        logger.error(f"Failed to create WebDriver for standalone test: {e}")
        return None

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(funcName)s - %(message)s')
    
    standalone_driver = _standalone_dod_create_driver(headless=False)
    if standalone_driver:
        try:
            scraped_data = fetch_dod_sbir_sttr_topics(driver=standalone_driver)
            if scraped_data:
                print(f"\n--- Scraped {len(scraped_data)} DoD SBIR/STTR Opportunities ---")
                print(json.dumps(scraped_data, indent=2))
            else:
                print("\nNo 'Open' DoD SBIR/STTR opportunities found.")
        finally:
            standalone_driver.quit()
    else:
        print("Failed to create WebDriver for standalone test.")

# afwerx_module.py
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
from selenium.common.exceptions import TimeoutException, NoSuchElementException
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

def fetch_afwerx_opportunities(driver_instance: WebDriver) -> list:
    """
    Scrapes the AFWERX "Current Efforts" page for all opportunities.
    This version scrapes all opportunities without keyword filtering.
    """
    module_name = "AFWERX"
    site_url = "https://afwerxchallenge.com/current-efforts/"
    results = []

    if not driver_instance:
        logger.error(f"[{module_name}] No WebDriver instance provided. Cannot proceed.")
        return []

    main_page_config = {
        "wait_for_selector": "featured-content div.featured-content-card",
        "card_selector": "featured-content", 
        "link_selector_within_card": "a[href]",
        "title_selector_on_listing": "div.card-title",
        "wait_time": 60,
        "subpage_load_delay": 5,
        "max_links_to_follow": 25
    }
    detail_page_selectors = {
        "title_selector": "h1.title, h1.challenge-title, div.title-holder h1, h1.entry-title",
        "desc_selector": "div.challenge-description, div.description-content, section.overview-section, div.fr-view, article#main-content"
    }

    logger.info(f"[{module_name}] 🔍 Scraping from {site_url}...")

    try:
        driver_instance.get(site_url)
        time.sleep(5) 

        cookie_button_selectors = [
            "button#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
            "button[data-testid='cookie-accept-all-button']"
        ]
        for selector in cookie_button_selectors:
            try:
                cookie_button = WebDriverWait(driver_instance, 7).until(EC.element_to_be_clickable((By.CSS_SELECTOR, selector)))
                driver_instance.execute_script("arguments[0].click();", cookie_button)
                logger.info(f"[{module_name}] Clicked a pop-up button with: {selector}.")
                time.sleep(3)
                break
            except TimeoutException:
                pass
        
        WebDriverWait(driver_instance, main_page_config['wait_time']).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, main_page_config['wait_for_selector']))
        )
        logger.info(f"[{module_name}] Cards component ready.")
        
        opportunity_card_host_elements = driver_instance.find_elements(By.CSS_SELECTOR, main_page_config['card_selector'])
        logger.info(f"[{module_name}] Found {len(opportunity_card_host_elements)} <{main_page_config['card_selector']}> host elements.")

        links_to_visit = []
        for card_host_el in opportunity_card_host_elements:
            try:
                link_el = card_host_el.find_element(By.CSS_SELECTOR, main_page_config['link_selector_within_card'])
                href = link_el.get_attribute("href")
                listing_title = card_host_el.find_element(By.CSS_SELECTOR, main_page_config['title_selector_on_listing']).text.strip()
                if href:
                    full_url = urljoin(site_url, href)
                    if full_url not in [item['url'] for item in links_to_visit]:
                        links_to_visit.append({'url': full_url, 'listing_title': listing_title})
            except Exception as e_card:
                logger.error(f"[{module_name}] Error processing card: {e_card}", exc_info=False)
        
        logger.info(f"[{module_name}] Extracted {len(links_to_visit)} unique links.")

        for idx, link_info in enumerate(links_to_visit[:main_page_config['max_links_to_follow']]):
            link_url = link_info['url']
            listing_title = link_info['listing_title']
            logger.info(f"[{module_name}] Processing subpage {idx+1}/{len(links_to_visit)}: {link_url}")

            try:
                driver_instance.get(link_url)
                WebDriverWait(driver_instance, 30).until(EC.presence_of_element_located((By.CSS_SELECTOR, "body")))
                time.sleep(main_page_config['subpage_load_delay'])
                sub_soup = BeautifulSoup(driver_instance.page_source, "html.parser")

                final_title = listing_title
                title_el_detail = sub_soup.select_one(detail_page_selectors['title_selector'])
                if title_el_detail:
                    extracted_detail_title = title_el_detail.get_text(strip=True)
                    if extracted_detail_title and len(extracted_detail_title) > 5:
                        final_title = extracted_detail_title
                
                final_desc = ""
                desc_el_detail = sub_soup.select_one(detail_page_selectors['desc_selector'])
                text_for_date_extraction = ""
                
                if desc_el_detail:
                    text_for_date_extraction = desc_el_detail.get_text(separator=" ", strip=True)
                    for tag_to_remove in desc_el_detail(['script', 'style', 'button', 'form', 'nav', 'footer', 'header']):
                        if hasattr(tag_to_remove, 'decompose'): tag_to_remove.decompose()
                    final_desc = desc_el_detail.get_text(separator=" ", strip=True)
                
                final_desc_cleaned = ' '.join(final_desc.split())[:1000]

                # --- Date Extraction Logic ---
                open_date_str = "N/A"
                close_date_str = "N/A"
                if text_for_date_extraction:
                    open_date_match = re.search(r"opens on\s+(\d{1,2}/\d{1,2}/\d{4})", text_for_date_extraction, re.IGNORECASE)
                    if open_date_match:
                        try:
                            open_date_str = datetime.strptime(open_date_match.group(1), "%m/%d/%Y").strftime("%Y-%m-%d")
                        except ValueError: open_date_str = open_date_match.group(1) 

                    close_date_match = re.search(r"ends on\s+(\d{1,2}/\d{1,2}/\d{4})", text_for_date_extraction, re.IGNORECASE)
                    if close_date_match:
                        try:
                            close_date_str = datetime.strptime(close_date_match.group(1), "%m/%d/%Y").strftime("%Y-%m-%d")
                        except ValueError: close_date_str = close_date_match.group(1)
                
                logger.info(f"✅ [{module_name}] Scraping '{final_title[:60]}'")
                results.append({
                    "Source": "AFWERX",
                    "Title": final_title,
                    "Description": final_desc_cleaned,
                    "URL": link_url,
                    "Open Date": open_date_str,
                    "Close Date": close_date_str,
                    "ScrapedDate": datetime.now().isoformat()
                })
            except Exception as e_subpage:
                logger.error(f"[{module_name}] Error processing subpage {link_url}: {e_subpage}", exc_info=False)
    
    except Exception as e_main:
        logger.error(f"[{module_name}] Main scrape failed: {e_main}", exc_info=True)

    logger.info(f"[{module_name}] Finished. Found {len(results)} total opportunities to be analyzed.")
    return results

# --- Standalone Testing ---
def _standalone_afwerx_create_driver(headless_mode=True):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    options = Options()
    if headless_mode: options.add_argument('--headless=new')
    options.add_argument(f'user-agent={MODULE_DEFAULT_HEADERS["User-Agent"]}')
    driver = webdriver.Chrome(options=options) 
    return driver

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(funcName)s - %(message)s')
    logger.info(f"🚀 Running {__name__} standalone for AFWERX testing...")

    standalone_driver_afwerx = _standalone_afwerx_create_driver(headless_mode=False)

    if standalone_driver_afwerx:
        try:
            # Call the updated function
            test_opportunities = fetch_afwerx_opportunities(driver_instance=standalone_driver_afwerx)
            if test_opportunities:
                logger.info(f"\n--- Scraped {len(test_opportunities)} AFWERX Opportunities (Standalone Test) ---")
                print(json.dumps(test_opportunities, indent=2))
            else:
                logger.info("No AFWERX opportunities found during standalone test.")
        finally:
            standalone_driver_afwerx.quit()
    else:
        logger.error("Failed to create driver for AFWERX standalone test.")
    logger.info(f"🏁 Standalone test for {__name__} finished.")

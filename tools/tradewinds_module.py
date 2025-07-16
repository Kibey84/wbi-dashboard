# tradewinds_module.py
import time
import logging
import json
from bs4 import BeautifulSoup, Tag
from urllib.parse import urljoin
from datetime import datetime
from typing import Optional

# Selenium Imports
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

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

def fetch_tradewinds_opportunities(driver: WebDriver) -> list:
    """
    Fetches all Tradewinds AI opportunities using a provided Selenium WebDriver instance.
    This version scrapes all opportunities without keyword filtering.
    """
    module_name = "Tradewinds"
    site_url = "https://www.tradewindai.com/opportunities"
    results = []

    if not driver:
        logger.error(f"[{module_name}] No WebDriver instance provided. Cannot proceed.")
        return []

    logger.info(f"[{module_name}] 🔍 Scraping from {site_url}...")

    try:
        driver.get(site_url)
        time.sleep(4)

        try:
            no_opps_message_xpath = "//*[contains(text(), 'No open opportunities. Please check back later.')]"
            WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.XPATH, no_opps_message_xpath)))
            logger.info(f"[{module_name}] Found 'No open opportunities' message. Exiting.")
            return results
        except TimeoutException:
            logger.info(f"[{module_name}] Proceeding to look for opportunity cards.")

        card_selector = "div.bg-white.rounded-lg.shadow-md"
        WebDriverWait(driver, 20).until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, card_selector)))
        
        opportunity_card_elements = driver.find_elements(By.CSS_SELECTOR, card_selector)
        logger.info(f"[{module_name}] Found {len(opportunity_card_elements)} opportunity card elements.")

        links_to_visit = []
        for card_element in opportunity_card_elements:
            card_html = card_element.get_attribute('outerHTML')
            if not card_html:
                continue
                
            card_soup = BeautifulSoup(card_html, 'html.parser')
            link_tag = card_soup.select_one("a.absolute.inset-0")

            if link_tag and isinstance(link_tag, Tag) and link_tag.has_attr('href'):
                href = str(link_tag.get('href', ''))
                full_url = urljoin(site_url, href)
                
                if full_url not in [item['url'] for item in links_to_visit]:
                    listing_title_tag = card_soup.select_one("h1, h2, h3")
                    listing_desc_tag = card_soup.select_one("div.line-clamp-4, p")
                    links_to_visit.append({
                        'url': full_url, 
                        'listing_title': listing_title_tag.get_text(strip=True) if listing_title_tag else "Title Hint N/A",
                        'listing_desc': listing_desc_tag.get_text(strip=True) if listing_desc_tag else ""
                    })

        logger.info(f"[{module_name}] Extracted {len(links_to_visit)} unique links to process.")

        for idx, link_info in enumerate(links_to_visit):
            link_url, listing_title, listing_desc = link_info['url'], link_info['listing_title'], link_info['listing_desc']
            logger.info(f"[{module_name}] Processing subpage {idx+1}/{len(links_to_visit)}: {link_url}")
            
            try:
                driver.get(link_url)
                WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, "body")))
                time.sleep(4)
                sub_soup = BeautifulSoup(driver.page_source, "html.parser")
                
                title_el = sub_soup.select_one("h1, h2")
                final_title = title_el.get_text(strip=True) if title_el and isinstance(title_el, Tag) else listing_title
                
                desc_el = sub_soup.select_one("div.prose, article, main")
                final_desc = desc_el.get_text(separator=" ", strip=True) if desc_el and isinstance(desc_el, Tag) else listing_desc
                final_desc_cleaned = ' '.join(final_desc.split())

                # AI will do the filtering, so we add every opportunity found
                logger.info(f"[{module_name}]  scraping '{final_title[:60]}'")
                results.append({
                    "Source": "Tradewinds",
                    "Title": final_title,
                    "Description": final_desc_cleaned,
                    "URL": link_url,
                    "ScrapedDate": datetime.now().isoformat()
                })

            except Exception as e_subpage:
                logger.error(f"[{module_name}] Error processing subpage {link_url}: {e_subpage}", exc_info=False)

    except Exception as e_main:
        logger.error(f"[{module_name}] Main scrape failed: {e_main}", exc_info=True)

    logger.info(f"[{module_name}] Finished. Found {len(results)} total opportunities to be analyzed.")
    return results

def _standalone_tradewinds_create_driver(headless_mode=True):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    options = Options()
    if headless_mode: options.add_argument('--headless=new')
    options.add_argument(f'user-agent={MODULE_DEFAULT_HEADERS["User-Agent"]}')
    driver = webdriver.Chrome(options=options)
    return driver

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(funcName)s - %(message)s')
    logger.info(f"🚀 Running standalone test for Tradewinds module...")

    standalone_driver = _standalone_tradewinds_create_driver(headless_mode=False)

    if standalone_driver:
        try:
            # Call the updated function without keyword arguments
            test_opportunities = fetch_tradewinds_opportunities(driver=standalone_driver)
            if test_opportunities:
                logger.info(f"\n--- Scraped {len(test_opportunities)} Tradewinds Opportunities ---")
                print(json.dumps(test_opportunities, indent=2))
            else:
                logger.info("No Tradewinds opportunities were found.")
        finally:
            logger.info("Closing test browser...")
            standalone_driver.quit()
    else:
        logger.error("Failed to create driver for standalone test.")

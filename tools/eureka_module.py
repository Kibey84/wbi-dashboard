# eureka_module.py

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
from selenium.common.exceptions import TimeoutException, NoSuchElementException 
from selenium.webdriver.remote.webdriver import WebDriver

from bs4 import BeautifulSoup
from bs4.element import Tag

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

def fetch_eureka_opportunities(driver: WebDriver):
    """
    Scrapes the EUREKA 'opencalls' page for opportunities.
    This version scrapes all opportunities and filters out those with past closing dates.
    """
    SITE_CONFIG_EUREKA = {
        "name": "EUREKA",
        "url": "https://eurekanetwork.org/opencalls/",
        "link_selector": "div.bg-white.group > a[href]",
        "title_selector_detail": "h1.heading-xl, h1.font-bold, h2.call-title, h1.text-3xl, header.wp-block-post-title h1",
        "max_links_to_follow": 25 
    }
    site_name = SITE_CONFIG_EUREKA["name"]
    site_url = SITE_CONFIG_EUREKA["url"]
    logger.info(f"[{site_name}] 🔍 Scraping from {site_url}...")
    results = []
    
    if not driver:
        logger.error(f"[{site_name}] No WebDriver instance provided. Skipping scrape.")
        return results

    try:
        driver.get(site_url)
        logger.info(f"[{site_name}] Page loaded: {driver.current_url}")
        cards_container_selector = "div.grid.grid-cols-1"
        try:
            WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.CSS_SELECTOR, cards_container_selector)))
            logger.info(f"[{site_name}] Main card container found.")
            time.sleep(2)
        except TimeoutException:
            logger.warning(f"[{site_name}] Timeout waiting for main card container ('{cards_container_selector}').")
            return results

        link_elements = driver.find_elements(By.CSS_SELECTOR, SITE_CONFIG_EUREKA["link_selector"])
        links_to_visit = []
        for el_idx, el in enumerate(link_elements):
            href = el.get_attribute("href")
            text_hint = f"Link {el_idx+1}"
            try:
                title_hint_el = el.find_element(By.XPATH, ".//h3[contains(@class, 'heading-sm')] | .//h2[contains(@class, 'heading-md')]")
                if title_hint_el and title_hint_el.text.strip(): 
                    text_hint = title_hint_el.text.strip()
            except NoSuchElementException: 
                logger.debug(f"[{site_name}] Title hint element not found for link {el_idx+1}.")
            
            if href:
                full_url = urljoin(site_url, href)
                if full_url not in [l_info['url'] for l_info in links_to_visit]:
                    links_to_visit.append({"url": full_url, "text_hint": text_hint})
        
        max_links = SITE_CONFIG_EUREKA["max_links_to_follow"]
        logger.info(f"[{site_name}] Extracted {len(links_to_visit)} unique links (processing up to {max_links}).")

        for idx, link_info in enumerate(links_to_visit[:max_links]):
            detail_url = link_info["url"]
            listing_title_hint = link_info["text_hint"]
            logger.info(f"[{site_name}] Processing detail page {idx+1}/{len(links_to_visit)}: {detail_url}")
            closing_date_obj = None
            closing_date_str = "N/A"

            try:
                driver.get(detail_url)
                WebDriverWait(driver, 25).until(EC.presence_of_element_located((By.CSS_SELECTOR, "body")))
                time.sleep(3) 
                page_soup = BeautifulSoup(driver.page_source, "html.parser")
                
                title_el = page_soup.select_one(SITE_CONFIG_EUREKA["title_selector_detail"])
                title_text = title_el.get_text(strip=True) if title_el and title_el.get_text(strip=True) else listing_title_hint
                
                # --- Date Extraction Logic ---
                deadline_banner_el = page_soup.find(
                    lambda tag: isinstance(tag, Tag) and tag.name == 'div' and "deadline:" in tag.get_text(strip=True).lower()
                )
                
                if deadline_banner_el:
                    date_match = re.search(r"Deadline:\s*(\d{1,2}\s+\w+\s+\d{4})", deadline_banner_el.get_text(strip=True), re.IGNORECASE)
                    if date_match:
                        try:
                            closing_date_obj = datetime.strptime(date_match.group(1), "%d %B %Y")
                        except ValueError: 
                            logger.warning(f"[{site_name}] Could not parse date from banner: '{date_match.group(1)}'")

                if not closing_date_obj: 
                    until_label = page_soup.find(lambda tag: isinstance(tag, Tag) and tag.name == 'p' and "Until:" in tag.get_text(strip=True))
                    if until_label and until_label.find_next_sibling('p'):
                        date_str_extracted = until_label.find_next_sibling('p').get_text(strip=True) #type: ignore
                        try:
                            closing_date_obj = datetime.strptime(date_str_extracted.strip(), "%d %B %Y")
                        except ValueError: 
                            logger.warning(f"[{site_name}] Could not parse date from 'Until:' field: '{date_str_extracted}'")
                
                if closing_date_obj:
                    closing_date_str = closing_date_obj.strftime("%Y-%m-%d")
                    if closing_date_obj < datetime.now():
                        logger.info(f"[{site_name}] Skipping '{title_text[:60]}' as its closing date {closing_date_str} has passed.")
                        continue
                
                # --- Description Extraction ---
                desc_el = page_soup.select_one("div.prose, div.wysiwyg-content, article.post-content, div.entry-content, main#main")
                desc_content_cleaned = "Description Not Found"
                if desc_el:
                    for selector in ["nav", "footer", "header", "aside", ".sidebar", "form", "div[class*='share']", "div[class*='related']", "div[class*='meta']"]:
                        for unwanted in desc_el.select(selector): unwanted.decompose()
                    desc_content = desc_el.get_text(separator=" ", strip=True)
                    desc_content_cleaned = ' '.join(desc_content.split())
                
                logger.info(f"✅ [{site_name}] Scraping '{title_text[:60]}'")
                results.append({
                    "Source": site_name, "Title": title_text,
                    "Description": desc_content_cleaned, "URL": detail_url,
                    "ScrapedDate": datetime.now().isoformat(),
                    "Close Date": closing_date_str 
                })
            except Exception as e_subpage: 
                logger.warning(f"[{site_name}] Error processing subpage {detail_url}: {e_subpage}", exc_info=False)
                
    except Exception as e_main:
        logger.error(f"[{site_name}] Main scrape failed: {e_main}", exc_info=True)
    
    logger.info(f"[{site_name}] Finished. Found {len(results)} total opportunities to be analyzed.")
    return results

def _standalone_eureka_create_driver(headless=True):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options as ChromeOptionsStandalone
    options = ChromeOptionsStandalone()
    if headless: options.add_argument('--headless=new')
    options.add_argument(f'user-agent={MODULE_DEFAULT_HEADERS["User-Agent"]}')
    try:
        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(90) 
        return driver
    except Exception as e:
        logger.error(f"Failed to create WebDriver for EUREKA standalone test: {e}", exc_info=True)
        return None

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(funcName)s - %(message)s')
    logger.info("Running EUREKA Opportunities Scraper standalone for testing...")
    
    is_headless_test = False
    standalone_driver_eureka = _standalone_eureka_create_driver(headless=is_headless_test) 
    
    if standalone_driver_eureka:
        try:
            scraped_data = fetch_eureka_opportunities(driver=standalone_driver_eureka)
            
            if scraped_data:
                print(f"\n--- Scraped {len(scraped_data)} EUREKA Opportunities ---")
                print(json.dumps(scraped_data, indent=2))
            else:
                print("\nNo EUREKA opportunities found (Standalone Test).")
        except Exception as e_test_main:
            logger.error(f"Error in EUREKA standalone test's main logic: {e_test_main}", exc_info=True)
        finally:
            logger.info("Standalone test finished.")
            time.sleep(1 if is_headless_test else 10)
            standalone_driver_eureka.quit()
    else:
        print("Failed to create driver for EUREKA standalone test.")

# arl_opportunities_module.py

import time
import logging
import json
from datetime import datetime
import os
import re
from typing import Optional

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, JavascriptException
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
from selenium import webdriver 

logger = logging.getLogger(__name__)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(funcName)s - %(message)s')
    _handler.setFormatter(_formatter)
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

def get_shadow_root_arl(driver_instance: WebDriver, host_element: WebElement) -> Optional[WebElement]:
    """Executes JavaScript to get the shadow root of a given web element."""
    try:
        return driver_instance.execute_script('return arguments[0].shadowRoot', host_element)
    except JavascriptException as e:
        logger.debug(f"ARL Module: Could not get shadowRoot. Error: {e}")
        return None

def get_arl_field_value(card_el: WebElement, label_text_exact: str, section_class_hint: str, field_nature="default") -> str:
    """Extracts a specific field value from within an opportunity card."""
    try:
        section = None
        # Find all potential sections that might contain the label
        candidate_sections_xpath = f".//div[contains(@class, '{section_class_hint.split(' ')[0]}')]"
        candidate_sections = card_el.find_elements(By.XPATH, candidate_sections_xpath)

        if not candidate_sections: return "N/A"
        
        # Find the correct section by matching the label text
        for sec_candidate in candidate_sections:
            try:
                label_span = sec_candidate.find_element(By.XPATH, "./span[1]")
                if label_span.text.strip() == label_text_exact:
                    section = sec_candidate
                    break
            except NoSuchElementException: continue
        
        if not section: return "N/A"

        if field_nature == "date":
            date_element = section.find_element(By.TAG_NAME, "lightning-formatted-date-time")
            raw_date_str = date_element.text.strip()
            # Basic date parsing can be added here if needed, or handled later
            return raw_date_str
        elif field_nature == "opp_type":
            value_el = section.find_element(By.CSS_SELECTOR, "span.GRLE-accented-section")
            return value_el.text.strip()
        else: # Default for other text fields
            value_divs = section.find_elements(By.XPATH, "./div[normalize-space()]")
            if value_divs: return value_divs[0].text.strip()
            return "N/A"

    except Exception as e_field:
        logger.error(f"ARL: Error extracting field '{label_text_exact}': {e_field}", exc_info=False) 
        return "N/A"


def fetch_arl_opportunities(driver_instance: WebDriver, max_items_arg: Optional[int] = None) -> list:
    """
    Scrapes the ARL Opportunities page for all available opportunities.
    """
    target_url = "https://cftste.experience.crmforce.mil/arlext/s/arl-opportunities"
    max_items_to_process = max_items_arg if isinstance(max_items_arg, int) else 20
    logger.info(f"ARL Module: Starting scrape of {target_url}, targeting up to {max_items_to_process} items.")
    scraped_opportunities = []
    items_processed_count = 0

    if not driver_instance:
        logger.error("ARL Module: No WebDriver instance provided.")
        return scraped_opportunities

    try:
        driver_instance.get(target_url)
        current_page_url_for_pseudo_links = driver_instance.current_url
        WebDriverWait(driver_instance, 45).until(EC.invisibility_of_element_located((By.CSS_SELECTOR, "div#auraLoadingBox")))
        time.sleep(5) 

        main_lwc_host = WebDriverWait(driver_instance, 90).until(EC.presence_of_element_located((By.XPATH, "//c-gen-record-list-display[@c-genrecordlistdisplay_genrecordlistdisplay-host]")))
        main_shadow_root = get_shadow_root_arl(driver_instance, main_lwc_host)
        if not main_shadow_root: 
            logger.error("ARL Module: Could not get main shadow root for LWC host.")
            return scraped_opportunities

        entry_list_element = WebDriverWait(main_shadow_root, 45).until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.ENTRY-LIST")))
        time.sleep(5) 
        lwc_opportunity_host_elements = entry_list_element.find_elements(By.CSS_SELECTOR, "c-gen-record-list-display-entry")
        logger.info(f"ARL Module: Found {len(lwc_opportunity_host_elements)} LWC opportunity hosts.")

        for host_idx, opp_host_element in enumerate(lwc_opportunity_host_elements):
            if items_processed_count >= max_items_to_process: 
                logger.info(f"ARL Module: Reached max items to process ({max_items_to_process}).")
                break
            
            item_shadow_root = get_shadow_root_arl(driver_instance, opp_host_element)
            if not item_shadow_root: 
                logger.warning(f"ARL Module: Could not get shadow root for item host {host_idx + 1}.")
                continue
            
            try:
                card = WebDriverWait(item_shadow_root, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.GRLE-display-wrapper")))
            except TimeoutException:
                logger.warning(f"ARL Module: Card wrapper not found in item host {host_idx + 1}.")
                continue
                
            items_processed_count += 1
            opportunity_data = {"Source": "ARL Opportunities"} 

            try:
                opportunity_data["Title"] = card.find_element(By.CSS_SELECTOR, "div.GRLE-title").text.strip()
                try:
                    opportunity_data["Announcement ID"] = card.find_element(By.CSS_SELECTOR, "div.GRLE-announceID").text.strip()
                except NoSuchElementException: opportunity_data["Announcement ID"] = "N/A"
                try:
                    desc_preview_host = card.find_element(By.CSS_SELECTOR, "div.GRLE-description-preview lightning-formatted-rich-text")
                    opportunity_data["Description"] = desc_preview_host.text.strip()[:1000]
                except NoSuchElementException: opportunity_data["Description"] = "N/A"
                
                opportunity_data["Opportunity Type"] = get_arl_field_value(card, "Opportunity Type", "GRLE-info")
                opportunity_data["ARL Office"] = get_arl_field_value(card, "ARL Office of Responsibility", "GRLE-info")
                opportunity_data["Published Date"] = get_arl_field_value(card, "Published Date", "GRLE-sidebar-section", field_nature="date") 
                opportunity_data["Close Date"] = get_arl_field_value(card, "Closing Date", "GRLE-sidebar-section", field_nature="date")

                pseudo_id_base = opportunity_data.get("Announcement ID", opportunity_data.get("Title", f"Item{host_idx}"))
                opportunity_data["URL"] = f"{current_page_url_for_pseudo_links}#{re.sub(r'[^a-zA-Z0-9_-]+', '_', pseudo_id_base)}" 
                
                opportunity_data["ScrapedDate"] = datetime.now().isoformat()
                
                logger.info(f"✅ [Scraping] ARL - '{opportunity_data['Title'][:60]}'")
                scraped_opportunities.append(opportunity_data)

            except Exception as e_card_content:
                logger.error(f"ARL Module: Error processing card content for item {host_idx + 1}: {e_card_content}", exc_info=False)

    except Exception as e:
        logger.error(f"ARL Module: Main scrape error: {e}", exc_info=True)

    logger.info(f"ARL Module: Finished. Scraped {len(scraped_opportunities)} items after processing {items_processed_count} cards.")
    return scraped_opportunities

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(name)s - %(filename)s:%(lineno)d - %(message)s')
    
    logger.info("🚀 Starting ARL Opportunities Module standalone test...")
    
    service_test = ChromeService(ChromeDriverManager().install())
    test_options = Options()
    test_options.add_argument("--start-maximized")

    test_driver_standalone = None
    try:
        test_driver_standalone = webdriver.Chrome(service=service_test, options=test_options)
    except Exception as e:
        logger.error(f"Standalone ARL: Failed to init WebDriver: {e}", exc_info=True)

    if test_driver_standalone:
        try:
            arl_opps_results_test = fetch_arl_opportunities(
                driver_instance=test_driver_standalone,
                max_items_arg=5
            )
            if arl_opps_results_test:
                logger.info(f"✅ ARL Test: Processed {len(arl_opps_results_test)} opportunities.")
                print(json.dumps(arl_opps_results_test, indent=2))
            else:
                logger.info("ℹ️ ARL Test: No opportunities scraped.")
        finally:
            test_driver_standalone.quit()
    logger.info("🏁 ARL Opportunities Module standalone test finished.")

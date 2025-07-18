# iarpa_scraper.py

import time
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import logging

def fetch_iarpa_opportunities(driver):
    """
    Scrapes the main IARPA Open BAA page for all listed opportunities.

    Args:
        driver: An instance of the Selenium WebDriver.

    Returns:
        A list of dictionaries, where each dictionary is a scraped opportunity.
    """
    url = "https://www.iarpa.gov/engage-with-us/open-baas"
    logging.info(f"Navigating to IARPA Open BAAs page: {url}")
    driver.get(url)
    opportunities = []

    try:
        wait = WebDriverWait(driver, 20)
        content_area = wait.until(EC.visibility_of_element_located((By.ID, "dnn_ctr497_View_ScopeWrapper")))

        baa_blocks = content_area.find_elements(By.TAG_NAME, "article")
        
        if not baa_blocks:
            logging.info("No active IARPA BAA blocks found on the page.")
            return []

        logging.info(f"Found {len(baa_blocks)} IARPA opportunity blocks. Scraping each one.")

        for block in baa_blocks:
            try:
                # --- Extract Title ---
                title_element = block.find_element(By.TAG_NAME, "h2")
                title = title_element.text.strip()

                # --- Extract URL ---
                link_element = title_element.find_element(By.TAG_NAME, "a")
                link_url = link_element.get_attribute('href')

                # --- Extract Description ---
                description_element = block.find_element(By.TAG_NAME, "p")
                description = description_element.text.strip()
                
                # --- Extract Dates (if available) ---
                close_date = "See BAA for details"

                opp = {
                    'Title': title,
                    'Description': description,
                    'URL': link_url,
                    'Close Date': close_date,
                    'Source': 'IARPA'
                }
                opportunities.append(opp)
                logging.info(f"Successfully scraped IARPA BAA: {title}")

            except Exception as e:
                logging.warning(f"Could not scrape an individual IARPA block: {e}")
                continue 

    except Exception as e:
        logging.error(f"Failed to scrape the main IARPA page: {e}", exc_info=True)

    return opportunities
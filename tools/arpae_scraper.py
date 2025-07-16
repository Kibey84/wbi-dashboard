# arpae_scraper.py
import time
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import logging

def fetch_arpae_opportunities(driver, url_to_scrape):
    """
    Scrapes a specific Funding Opportunity Announcement (FOA) from the ARPA-E portal.

    Args:
        driver: An instance of the Selenium WebDriver.
        url_to_scrape: The direct URL to the ARPA-E FOA.

    Returns:
        A list containing a dictionary with the scraped opportunity details.
    """
    logging.info(f"Navigating to ARPA-E opportunity: {url_to_scrape}")
    driver.get(url_to_scrape)
    opportunities = []
    
    try:
        # Wait for the main opportunity container to be visible
        wait = WebDriverWait(driver, 20)
        container = wait.until(EC.visibility_of_element_located((By.ID, "main")))
        
        # --- Extract the Title ---
        try:
            title_element = container.find_element(By.TAG_NAME, "h1")
            title = title_element.text.strip()
        except Exception as e:
            logging.warning(f"Could not find title for ARPA-E opportunity: {e}")
            title = "Title Not Found"

        # --- Extract the Description ---
        # The description is often in a div with a specific class or ID.
        # We will look for a div that contains the "Objective" heading.
        try:
            description_elements = container.find_elements(By.XPATH, "//div[contains(@class, 'display-field')]/p")
            description_parts = [p.text for p in description_elements if p.text.strip()]
            description = "\n".join(description_parts)
            if not description:
                description = "Description not found."
        except Exception as e:
            logging.warning(f"Could not extract description for ARPA-E opportunity: {e}")
            description = "Description extraction failed."
            
        # --- Extract Key Dates ---
        # Dates are typically in a table or definition list
        close_date = "Not Found"
        try:
            # Look for a strong tag that says "FOA Issue Date" and get its parent's text
            date_items = driver.find_elements(By.XPATH, "//div[contains(@class, 'foa-view-detail-value')]")
            if len(date_items) > 1:
                 #The second to last date is usually the close date
                close_date = date_items[-2].text.strip()
        except Exception as e:
            logging.warning(f"Could not find close date for ARPA-E opportunity: {e}")


        logging.info(f"Successfully scraped ARPA-E opportunity: {title[:50]}...")
        
        opp = {
            'Title': title,
            'Description': description,
            'URL': url_to_scrape,
            'Close Date': close_date,
            'Source': 'ARPA-E'
        }
        opportunities.append(opp)

    except Exception as e:
        logging.error(f"Failed to scrape the ARPA-E page at {url_to_scrape}: {e}", exc_info=True)

    return opportunities
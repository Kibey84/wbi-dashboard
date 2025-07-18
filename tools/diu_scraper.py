# diu_scraper.py

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from datetime import datetime
import time
import logging

def fetch_diu_opportunities(driver_instance):
    """
    Scrapes the DIU "Current Solicitations" page for open opportunities.
    
    Args:
        driver_instance: An active Selenium WebDriver instance.
        
    Returns:
        A list of dictionaries, where each dictionary is an opportunity.
    """
    url = "https://www.diu.mil/work-with-us/open-solicitations"
    logging.info(f"Navigating to DIU opportunities page: {url}")
    driver_instance.get(url)
    opportunities = []

    try:
        wait = WebDriverWait(driver_instance, 20)
        solicitations_container = wait.until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, "div.usa-accordion"))
        )
        
        solicitation_buttons = solicitations_container.find_elements(By.CSS_SELECTOR, "button.usa-accordion__button")

        if not solicitation_buttons:
            logging.info("No open solicitations found on the DIU page.")
            return []

        logging.info(f"Found {len(solicitation_buttons)} DIU solicitations.")

        for button in solicitation_buttons:
            try:
                title = button.text.strip()
                
                content_div = button.find_element(By.XPATH, "./parent::h4/following-sibling::div")
                
                description_paragraphs = content_div.find_elements(By.TAG_NAME, "p")
                description = "\n".join([p.text for p in description_paragraphs])
                
                link_element = content_div.find_element(By.PARTIAL_LINK_TEXT, "Submit a Solution")
                link_url = link_element.get_attribute('href')
                
                close_date_text = ""
                for p in description_paragraphs:
                    if "Submissions are due by" in p.text:
                        close_date_text = p.text.split("Submissions are due by")[1].split("at")[0].strip()
                        break
                
                opp = {
                    'Title': title,
                    'Description': description,
                    'URL': link_url,
                    'Close Date': close_date_text if close_date_text else "See Description",
                    'ScrapedDate': datetime.now().strftime('%Y-%m-%d')
                }
                opportunities.append(opp)
                logging.info(f"Successfully scraped DIU opportunity: {title}")
                
            except Exception as e:
                logging.warning(f"Could not parse a DIU solicitation block: {e}")
                continue

    except Exception as e:
        logging.error(f"Failed to scrape the DIU page: {e}", exc_info=True)
        
    return opportunities

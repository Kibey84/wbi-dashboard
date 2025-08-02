import logging
import time
import json
from datetime import datetime
from typing import Optional, List, Dict
from urllib.parse import urljoin
import random

from bs4 import BeautifulSoup, Tag

# --- Selenium Imports for browser automation ---
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium_stealth import stealth

# --- Logger Setup ---
logger = logging.getLogger(__name__)

# --- Module Configuration ---
ARL_OPPORTUNITIES_URL = "https://cftste.experience.crmforce.mil/arlext/s/arl-opportunities"

def fetch_arl_opportunities(max_items: int = 20) -> List[Dict]:
    """
    Fetches ARL opportunities using Selenium with Stealth to handle dynamic content and anti-bot measures.
    """
    logger.info(f"🔍 Fetching ARL Opportunities from {ARL_OPPORTUNITIES_URL}")
    results = []
    driver = None

    try:
        options = webdriver.ChromeOptions()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("start-maximized")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        
        stealth(driver,
                languages=["en-US", "en"],
                vendor="Google Inc.",
                platform="Win32",
                webgl_vendor="Intel Inc.",
                renderer="Intel Iris OpenGL Engine",
                fix_hairline=True,
                )
        
        driver.get(ARL_OPPORTUNITIES_URL)

        WebDriverWait(driver, 30).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "div.slds-card"))
        )
        
        soup = BeautifulSoup(driver.page_source, "html.parser")
        opportunity_cards = soup.select("div.slds-card")
        logger.info(f"Found {len(opportunity_cards)} cards on page.")

        for idx, card in enumerate(opportunity_cards[:max_items]):
            try:
                title_el = card.select_one("h2.slds-card__header-title")
                title = title_el.get_text(strip=True) if title_el else "N/A"

                desc_el = card.select_one("div.slds-card__body")
                desc = desc_el.get_text(strip=True, separator='\n')[:1500] if desc_el else "N/A"

                card_details = {}
                if desc_el:
                    for item in desc_el.select('div.slds-grid'):
                        label_el = item.select_one('span.slds-form-element__label')
                        value_el = item.select_one('div.slds-form-element__control span')
                        if label_el and value_el:
                            label = label_el.get_text(strip=True)
                            value = value_el.get_text(strip=True)
                            card_details[label] = value
                
                close_date = card_details.get("Closing Date", "N/A")
                open_date = card_details.get("Published Date", "N/A")

                if close_date != "N/A":
                    try:
                        if datetime.strptime(close_date, "%b %d, %Y") < datetime.now():
                            logger.info(f"Skipping '{title[:50]}...' (closed on {close_date})")
                            continue
                    except ValueError:
                        pass 

                result = {
                    "Source": "ARL Opportunities",
                    "Title": title,
                    "Description": desc,
                    "Open Date": open_date,
                    "Close Date": close_date,
                    "ScrapedDate": datetime.now().isoformat(),
                    "URL": ARL_OPPORTUNITIES_URL 
                }
                results.append(result)
                logger.info(f"✅ Scraped: {title[:50]}...")

            except Exception as e_card:
                logger.error(f"Error scraping card #{idx+1}: {e_card}")

    except TimeoutException:
        logger.error("[ARL] Timed out waiting for page content. The site may be down or blocking automation.")
    except WebDriverException as e:
        logger.error(f"[ARL] WebDriver error: {e}")
    except Exception as e:
        logger.error(f"Error fetching ARL Opportunities: {e}", exc_info=True)
    finally:
        if driver:
            driver.quit()

    logger.info(f"✅ Finished fetching {len(results)} opportunities.")
    return results

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    opps = fetch_arl_opportunities(5)
    print(json.dumps(opps, indent=2))

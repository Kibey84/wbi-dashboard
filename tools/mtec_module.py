import time
import logging
import re
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

# Logger setup
logger = logging.getLogger(__name__)

def _parse_mtec_date(date_text: str) -> str:
    """Parses various date formats found on the MTEC site."""
    if not date_text or not date_text.strip() or date_text.lower() in ['n/a', 'tbd']:
        return "N/A"

    cleaned = date_text.strip()
    for label in ["proposals due", "responses due", "closing date", "submission deadline",
                  "applications due", "deadline", "date offers due", "offers due"]:
        cleaned = re.sub(rf"^\s*{re.escape(label)}\s*[:\-]?\s*", "", cleaned, flags=re.IGNORECASE).strip()

    match = re.search(r"(\w+\s+\d{1,2},\s*\d{4}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\d{4}-\d{2}-\d{2})", cleaned)
    if match:
        date_str = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", match.group(1))
        for fmt in ["%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y", "%Y-%m-%d",
                    "%m/%d/%Y", "%m-%d-%Y", "%m.%d.%Y", "%m/%d/%y", "%m-%d-%y", "%m.%d.%y"]:
            try:
                dt_obj = datetime.strptime(date_str, fmt)
                if '%y' in fmt and dt_obj.year > datetime.now().year + 20:
                    dt_obj = dt_obj.replace(year=dt_obj.year - 100)
                return dt_obj.strftime("%Y-%m-%d")
            except ValueError:
                continue
    return "N/A"

def _get_details_from_page(driver: webdriver.Chrome, detail_url: str, listing_title: str) -> Optional[dict]:
    """Fetches and parses a single opportunity detail page using the Selenium driver."""
    try:
        driver.get(detail_url)
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.entry-content, article.content, main#main, div.post-content"))
        )
        soup = BeautifulSoup(driver.page_source, "html.parser")

        title_el = soup.select_one("h1.entry-title, h1.page-title, h1.title")
        final_title = title_el.get_text(strip=True) if isinstance(title_el, Tag) else listing_title

        desc_el = soup.select_one("div.post-content, div.entry-content")
        description = "Description not found."
        if isinstance(desc_el, Tag):
            for unwanted in desc_el.select('script, style, form, button, .related-posts'):
                unwanted.decompose()
            description = ' '.join(desc_el.get_text(separator=" ", strip=True).split())[:3500]

        close_date = "N/A"
        candidates = soup.find_all(string=re.compile(r"(proposals|responses|offers)\s+due|closing\s+date|submission\s+deadline", re.I))
        if candidates:
            parent = candidates[0].find_parent(['p', 'h1', 'h2', 'h3', 'h4', 'div'])
            if parent:
                close_date = _parse_mtec_date(parent.get_text())

        if close_date == "N/A":
            h6 = soup.select_one("h6.fusion-title-heading")
            if h6:
                close_date = _parse_mtec_date(h6.get_text())

        if close_date != "N/A":
            try:
                if datetime.strptime(close_date, "%Y-%m-%d") < datetime.now():
                    logger.info(f"Skipping (Past Close Date): {final_title}")
                    return None
            except ValueError:
                logger.warning(f"Could not parse date '{close_date}' for filtering.")

        logger.info(f"✅ Scraped: {final_title}")
        return {
            "Source": "MTEC",
            "Title": final_title,
            "Description": description,
            "URL": detail_url,
            "Close Date": close_date,
            "ScrapedDate": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"[MTEC Detail] Error parsing {detail_url}: {e}", exc_info=False)
        return None

def fetch_mtec_opportunities() -> list:
    logger.info("[MTEC] 🔍 Scraping MTEC opportunities (Selenium-Stealth)...")
    base_url = "https://www.mtec-sc.org/solicitations/"
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
        
        stealth(driver, languages=["en-US", "en"], vendor="Google Inc.", platform="Win32", webgl_vendor="Intel Inc.", renderer="Intel Iris OpenGL Engine", fix_hairline=True)
        
        driver.get(base_url)
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.post-content")))
        
        soup = BeautifulSoup(driver.page_source, "html.parser")
        posts = soup.select("div.post-content")
        logger.info(f"Found {len(posts)} MTEC posts.")

        for post in posts:
            title_el = post.select_one("h3 a")
            if isinstance(title_el, Tag) and title_el.has_attr('href'):
                link = str(title_el['href']).strip()
                listing_title = title_el.get_text(strip=True)
                
                detail = _get_details_from_page(driver, link, listing_title)
                if detail:
                    results.append(detail)
                time.sleep(random.uniform(1.0, 2.5))

    except TimeoutException:
        logger.error("[MTEC] Timed out waiting for page content. Login may be required or site is down.")
    except WebDriverException as e:
        logger.error(f"[MTEC] WebDriver error: {e}")
    except Exception as e:
        logger.error(f"Failed to fetch MTEC opportunities: {e}")
    finally:
        if driver:
            driver.quit()

    logger.info(f"Scraping complete. {len(results)} valid opportunities found.")
    return results

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', force=True)
    scraped_data = fetch_mtec_opportunities()
    print(f"\n--- Scraped {len(scraped_data)} MTEC Opportunities ---")
    if scraped_data:
        print(json.dumps(scraped_data, indent=2))
    else:
        logger.info("No opportunities found.")

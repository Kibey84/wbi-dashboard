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

# --- Import for bypassing bot detection ---
from selenium_stealth import stealth

# Logger setup
logger = logging.getLogger(__name__)

def _parse_nstxl_date(date_text: str) -> str:
    """Parses various date formats found on the NSTXL site."""
    if not date_text or not date_text.strip():
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
                return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
    return "N/A"

def fetch_nstxl_detail_page(driver: webdriver.Chrome, detail_url: str, listing_title: str) -> Optional[dict]:
    """Fetches and parses a single opportunity detail page using Selenium."""
    try:
        driver.get(detail_url)
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.post-content, div.entry-content"))
        )
        soup = BeautifulSoup(driver.page_source, "html.parser")

        title_el = soup.select_one("h1.entry-title, h1.fusion-post-title")
        final_title = title_el.get_text(strip=True) if title_el else listing_title

        desc_el = soup.select_one("div.post-content, div.entry-content")
        description = "Description not found."
        if desc_el:
            for unwanted in desc_el.select('script, style, form, button, .related-posts'):
                unwanted.decompose()
            description = ' '.join(desc_el.get_text(separator=" ", strip=True).split())[:3500]

        close_date = "N/A"
        candidates = soup.find_all(string=re.compile(r"(proposals|responses|offers)\s+due|closing\s+date|submission\s+deadline", re.I))
        if candidates:
            parent = candidates[0].find_parent(['p', 'h1', 'h2', 'h3', 'h4', 'div'])
            if parent:
                close_date = _parse_nstxl_date(parent.get_text())

        if close_date == "N/A":
            h6 = soup.select_one("h6.fusion-title-heading")
            if h6:
                close_date = _parse_nstxl_date(h6.get_text())
        
        if close_date != "N/A":
            try:
                if datetime.strptime(close_date, "%Y-%m-%d") < datetime.now():
                    logger.info(f"[NSTXL] Skipping (Past Close Date): {final_title}")
                    return None
            except ValueError:
                logger.warning(f"[NSTXL] Could not parse date '{close_date}' for filtering.")

        logger.info(f"✅ Scraped: {final_title}")
        return {
            "Source": "NSTXL",
            "Title": final_title,
            "Description": description,
            "URL": detail_url,
            "Close Date": close_date,
            "ScrapedDate": datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f"[NSTXL Detail] Error fetching detail page {detail_url}: {e}")
        return None

def fetch_nstxl_opportunities() -> list:
    logger.info("[NSTXL] 🔍 Scraping NSTXL opportunities (Selenium-Stealth)...")
    base_url = "https://nstxl.org/opportunities/"
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

        # --- Apply Stealth settings to the driver ---
        stealth(driver,
                languages=["en-US", "en"],
                vendor="Google Inc.",
                platform="Win32",
                webgl_vendor="Intel Inc.",
                renderer="Intel Iris OpenGL Engine",
                fix_hairline=True,
                )
        
        driver.get(base_url)

        WebDriverWait(driver, 20).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "h2.entry-title.fusion-post-title > a[href]"))
        )
        
        soup = BeautifulSoup(driver.page_source, "html.parser")
        links = soup.select("h2.entry-title.fusion-post-title > a[href]")
        
        opportunities_to_scrape = [{
            "url": urljoin(base_url, str(link.get('href')).strip()),
            "listing_title": link.get_text(strip=True)
        } for link in links if link.get('href')]

        logger.info(f"[NSTXL] Found {len(opportunities_to_scrape)} opportunities on listing page.")

        for idx, opp in enumerate(opportunities_to_scrape[:25]):
            logger.info(f"Processing {idx+1}/{len(opportunities_to_scrape)}: {opp['url']}")
            detail = fetch_nstxl_detail_page(driver, opp['url'], opp['listing_title'])
            if detail:
                results.append(detail)
            time.sleep(random.uniform(1.0, 2.5)) # Random delay

    except TimeoutException:
        logger.error("[NSTXL] Timed out waiting for page content to load.")
    except WebDriverException as e:
        logger.error(f"[NSTXL] WebDriver error: {e}")
    except Exception as e:
        logger.error(f"[NSTXL] ❌ Failed to scrape NSTXL: {e}", exc_info=True)
    finally:
        if driver:
            driver.quit()

    logger.info(f"[NSTXL] Done. {len(results)} valid opportunities collected.")
    return results

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', force=True)
    logger.info("🚀 Starting NSTXL scraper standalone...")

    scraped = fetch_nstxl_opportunities()
    if scraped:
        print(json.dumps(scraped, indent=2))
    else:
        logger.info("No opportunities found.")

import logging
import time
from bs4 import BeautifulSoup, Tag
from urllib.parse import urljoin
from datetime import datetime
import re
import json
from typing import Optional, List, Dict
import random

# --- Selenium Imports for browser automation ---
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium_stealth import stealth

# Setup logger
logger = logging.getLogger(__name__)

# --- Helper Functions ---
def _parse_date_from_nsin_line(line_text: str) -> Optional[datetime]:
    if not line_text:
        return None
    date_match = re.search(r"((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},\s*\d{4}|\d{1,2}/\d{1,2}/\d{2,4})", line_text, re.IGNORECASE)
    if date_match:
        date_str = date_match.group(1).replace(',', '')
        for fmt in ("%B %d %Y", "%b %d %Y", "%m/%d/%Y", "%m/%d/%y"):
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
    return None

def _parse_nsin_detail_page(driver: webdriver.Chrome, detail_url: str) -> tuple[str, str, Optional[datetime], Optional[datetime]]:
    title, description, open_date_obj, closing_date_obj = "Title Not Found", "Description Not Found", None, None
    try:
        driver.get(detail_url)
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "h1.entry-title, h1.page-title, h1.title"))
        )
        soup = BeautifulSoup(driver.page_source, "html.parser")

        title_el = soup.select_one("h1.entry-title, h1.page-title, h1.title")
        title = title_el.get_text(strip=True) if isinstance(title_el, Tag) else "Title Not Found"

        desc_el = soup.select_one("div.entry-content, article.content, main#main, div.post-content")
        if desc_el and isinstance(desc_el, Tag):
            key_dates_section = desc_el.find(lambda tag: tag.name in ['h1','h2','h3','h4','h5','h6'] and "key dates" in tag.get_text(strip=True).lower())
            if key_dates_section:
                sibling = key_dates_section.find_next_sibling(['ul', 'div', 'p'])
                if isinstance(sibling, Tag):
                    for item in sibling.find_all(['li', 'p']):
                        line = item.get_text(strip=True)
                        if any(x in line.lower() for x in ["due", "close", "deadline", "end"]):
                            closing_date_obj = _parse_date_from_nsin_line(line)
                        if any(x in line.lower() for x in ["open", "launch"]):
                            open_date_obj = _parse_date_from_nsin_line(line)
            
            for unwanted in desc_el.select('div.social-share-group'):
                unwanted.decompose()
            description = ' '.join(desc_el.get_text(separator=" ", strip=True).split())[:3500]

    except Exception as e:
        logger.error(f"[NSIN Detail] Error parsing detail page {detail_url}: {e}", exc_info=True)

    return title, description, open_date_obj, closing_date_obj

def fetch_nsin_opportunities() -> List[Dict]:
    logger.info("[NSIN] 🔍 Scraping NSIN event opportunities (Selenium-Stealth)...")
    base_url = "https://www.nsin.mil/events/"
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
        
        driver.get(base_url)
        
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.posts-grid__container"))
        )
        
        soup = BeautifulSoup(driver.page_source, "html.parser")
        container = soup.select_one("div.posts-grid__container")

        if not container or "no events found" in container.get_text(strip=True).lower():
            logger.info("[NSIN] No events available.")
            return results

        event_cards = container.select("article.post--color-event")
        logger.info(f"[NSIN] Found {len(event_cards)} event cards.")

        for card in event_cards:
            if not isinstance(card, Tag):
                continue
            
            link_el = card.select_one("h3.post__title a")
            if link_el and link_el.has_attr("href"):
                href_val = str(link_el.get("href", "")).strip()
                if href_val:
                    detail_url = urljoin(base_url, href_val)
                    listing_title = link_el.get_text(strip=True)

                    title, desc, open_date, close_date = _parse_nsin_detail_page(driver, detail_url)
                    final_title = title if title != "Title Not Found" else listing_title

                    if close_date and close_date < datetime.now():
                        logger.info(f"[NSIN] Skipping '{final_title[:60]}' (closed on {close_date.strftime('%Y-%m-%d')})")
                        continue

                    logger.info(f"[NSIN] ✅ Scraping '{final_title[:60]}'")
                    results.append({
                        "Source": "NSIN",
                        "Title": final_title,
                        "Description": desc,
                        "URL": detail_url,
                        "ScrapedDate": datetime.now().isoformat(),
                        "Open Date": open_date.strftime("%Y-%m-%d") if open_date else "N/A",
                        "Close Date": close_date.strftime("%Y-%m-%d") if close_date else "N/A"
                    })
                    time.sleep(random.uniform(1.0, 2.5)) 

    except TimeoutException:
        logger.error("[NSIN] Timed out waiting for page content to load. The site may be down or blocking automation.")
    except WebDriverException as e:
        logger.error(f"[NSIN] WebDriver error: {e}")
    except Exception as e:
        logger.error(f"[NSIN] ❌ Failed to scrape NSIN: {e}", exc_info=True)
    finally:
        if driver:
            driver.quit()

    logger.info(f"[NSIN] Done. {len(results)} opportunities collected.")
    return results

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    data = fetch_nsin_opportunities()
    print(f"\n--- Scraped {len(data)} NSIN Opportunities ---")
    print(json.dumps(data, indent=2))

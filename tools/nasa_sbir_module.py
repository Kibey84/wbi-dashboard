# nasa_sbir_module.py
# ====== IMPORTS ======
import time
import logging
from urllib.parse import urljoin
from datetime import datetime
import re
import json
from typing import Optional

# Selenium Imports
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.remote.webdriver import WebDriver # For type hinting
from bs4 import BeautifulSoup, Tag

# Module-specific logger
logger = logging.getLogger(__name__)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(funcName)s - %(message)s')
    _handler.setFormatter(_formatter)
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

DEFAULT_HEADERS_NASA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
}

def _parse_nasa_date(date_str: str, current_datetime_obj: datetime, module_name_for_log="NASA SBIR Module", date_type="Date") -> Optional[datetime]:
    """Parses a date string from the NASA schedule table and returns a datetime object."""
    if not date_str or date_str.strip() in ["—", "-"]:
        return None

    primary_date_match = re.match(r"(\d{1,2}/\d{1,2}/\d{2,4}|\w+\s+\d{1,2}(?:st|nd|rd|th)?(?:,)?\s*\d{4}|\d{4}-\d{1,2}-\d{1,2})", date_str.strip(), re.IGNORECASE)
    date_str_cleaned = primary_date_match.group(1) if primary_date_match else date_str.strip()

    non_explicit_terms = ["tbd", "n/a", "various", "summer", "spring", "fall", "winter", "early", "mid", "late"]
    if any(term in date_str_cleaned.lower() for term in non_explicit_terms) and not primary_date_match: 
        return None
    
    possible_formats = [
        "%B %d, %Y", "%b %d, %Y", "%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y",
        "%d %B %Y", "%d %b %Y", "%m/%d/%y", "%m-%d-%y"
    ]
    
    text_to_parse = date_str_cleaned.replace(',', '')

    for fmt in possible_formats:
        try:
            dt = datetime.strptime(text_to_parse, fmt)
            if '%y' in fmt.lower() and '%Y' not in fmt:
                if dt.year > current_datetime_obj.year + 20: 
                    dt = dt.replace(year=dt.year - 100)
            return dt
        except ValueError:
            continue

    logger.warning(f"[{module_name_for_log}] Could not parse date string '{date_str_cleaned}' for {date_type}.")
    return None


def fetch_nasa_sbir_opportunities(driver: WebDriver, headers: dict):
    """
    Scrapes the NASA SBIR/STTR schedule page for currently open solicitations.
    This version scrapes all open opportunities without keyword filtering.
    """
    SCHEDULE_PAGE_URL = "https://www.nasa.gov/sbir-sttr-program/solicitations-and-opportunities/"
    module_name = "NASA SBIR Module" 
    logger.info(f"[{module_name}] Scraping {SCHEDULE_PAGE_URL} for solicitations...")
    results = []
    
    if not driver:
        logger.error(f"[{module_name}] No WebDriver instance provided. Cannot proceed.")
        return results

    solicitation_category_pages_to_visit = []
    current_datetime = datetime.now()

    try:
        driver.get(SCHEDULE_PAGE_URL)
        logger.info(f"[{module_name}] Page loaded: {driver.current_url}")
        
        table_figure_element = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH, "//figure[contains(@class, 'wp-block-table')]//table"))
        )
        rows = table_figure_element.find_elements(By.XPATH, ".//tbody/tr")
        logger.info(f"[{module_name}] Found {len(rows)} rows in schedule table.")
        
        for row_idx, row_element in enumerate(rows):
            try:
                cells = row_element.find_elements(By.TAG_NAME, "td")
                if len(cells) < 3: continue
                
                opportunity_cell_text_raw = cells[0].text
                open_date_str_from_table = cells[1].text.strip()
                close_date_str_from_table = cells[2].text.strip()

                name_from_table = opportunity_cell_text_raw.strip()
                href = None
                try:
                    link_element = cells[0].find_element(By.TAG_NAME, "a")
                    href = link_element.get_attribute("href")
                    name_from_table = link_element.text.strip() 
                except NoSuchElementException:
                    logger.debug(f"[{module_name}] Row {row_idx+1}: No <a> tag. Using full cell text.")
                
                if not href: continue
                    
                parsed_open_dt = _parse_nasa_date(open_date_str_from_table, current_datetime, module_name, "Open")
                parsed_close_dt = _parse_nasa_date(close_date_str_from_table, current_datetime, module_name, "Close")

                if isinstance(parsed_open_dt, datetime) and isinstance(parsed_close_dt, datetime):
                    if not (parsed_open_dt <= current_datetime <= parsed_close_dt):
                        logger.info(f"[{module_name}] SKIPPING (Not currently open): '{name_from_table}'.")
                        continue
                else:
                    logger.info(f"[{module_name}] SKIPPING (Ambiguous dates): '{name_from_table}'.")
                    continue
                
                logger.info(f"[{module_name}] ADDING Category '{name_from_table}' for detail visit.")
                solicitation_category_pages_to_visit.append({
                    "url": href, "category_name": name_from_table,
                    "formatted_open_date": parsed_open_dt.strftime('%Y-%m-%d'), 
                    "formatted_close_date": parsed_close_dt.strftime('%Y-%m-%d') 
                })
            except Exception as e_row:
                logger.error(f"[{module_name}] Error processing table row {row_idx+1}: {e_row}", exc_info=True)

        logger.info(f"[{module_name}] Will visit {len(solicitation_category_pages_to_visit)} category detail pages.")
        
        for item_to_visit in solicitation_category_pages_to_visit:
            detail_url = item_to_visit["url"]
            logger.info(f"Visiting detail page: {detail_url}")
            try:
                driver.get(detail_url)
                WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.entry-content, article.content, main#main-content")))
                page_soup = BeautifulSoup(driver.page_source, "html.parser")
                
                page_title_tag = page_soup.select_one("h1.wp-block-post-title, h1.page-title, h1.entry-title, main#main-content h1")
                page_title = page_title_tag.get_text(strip=True) if page_title_tag else item_to_visit["category_name"]

                desc_area_bs = page_soup.select_one("div.entry-content, article.content, main#main-content div.body-content") or page_soup.select_one("main#main-content, article, section#page")
                description_content = "Description not found."
                if desc_area_bs:
                    for unwanted_tag_selector in ["header", "footer", "nav", "aside", ".wp-block-navigation", ".site-header", ".site-footer", ".breadcrumb", "div.tablepress-responsive-wrapper", "div.addtoany_share_save_container"]:
                        for unwanted in desc_area_bs.select(unwanted_tag_selector):
                            unwanted.decompose()
                    description_content = desc_area_bs.get_text(separator=" ", strip=True)
                
                logger.info(f"✅ [{module_name}] Scraping '{page_title[:60]}'")
                results.append({
                    "Source": "NASA SBIR/STTR", "Title": page_title,
                    "Description": ' '.join(description_content.split())[:3500],
                    "URL": detail_url, "Open Date": item_to_visit["formatted_open_date"], 
                    "Close Date": item_to_visit["formatted_close_date"], "ScrapedDate": current_datetime.isoformat() 
                })
            except Exception as e_detail_page:
                logger.error(f"[{module_name}] Error processing detail page {detail_url}: {e_detail_page}", exc_info=True)
            time.sleep(0.5)

    except Exception as e_main_nasa:
        logger.error(f"[{module_name}] Main NASA scrape failed: {e_main_nasa}", exc_info=True)
        
    return results

def _standalone_nasa_create_driver(headless=True):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    options = Options()
    if headless: options.add_argument('--headless=new')
    options.add_argument(f'user-agent={DEFAULT_HEADERS_NASA["User-Agent"]}')
    driver = webdriver.Chrome(options=options)
    return driver

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(funcName)s - %(message)s')
    
    is_headless_test = False
    standalone_driver_nasa = _standalone_nasa_create_driver(headless=is_headless_test)

    if standalone_driver_nasa:
        try:
            # Call the updated function
            scraped_data_nasa = fetch_nasa_sbir_opportunities(
                driver=standalone_driver_nasa,
                headers=DEFAULT_HEADERS_NASA
            )
            if scraped_data_nasa:
                print(f"\n--- Scraped {len(scraped_data_nasa)} NASA SBIR/STTR Opportunities ---")
                print(json.dumps(scraped_data_nasa, indent=2))
            else:
                print("\nNo currently open NASA SBIR/STTR opportunities found.")
        except Exception as e_standalone:
            logger.error(f"Error during NASA standalone test: {e_standalone}", exc_info=True)
        finally:
            logger.info("Standalone test finished.")
            time.sleep(1 if is_headless_test else 10)
            standalone_driver_nasa.quit()
    else:
        print("Failed to create driver for NASA standalone test.")

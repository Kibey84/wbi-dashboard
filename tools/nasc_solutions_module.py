# nasc_solutions_module.py

import logging
import requests
from bs4 import BeautifulSoup, Tag
from urllib.parse import urljoin
import time
from datetime import datetime
import re
import json
from typing import Optional

# --- Logger Setup ---
logger = logging.getLogger(__name__)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(funcName)s - %(message)s')
    _handler.setFormatter(_formatter)
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    
# --- Module Configuration ---
NASC_OPPORTUNITIES_URL = "https://nascsolutions.tech/opportunities/"
REQUEST_DELAY_SECONDS = 1

MODULE_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 OpportunityScraperModule/1.0"
}

def _parse_nasc_date(date_str: str) -> str:
    """Parses a date string and returns it in YYYY-MM-DD format."""
    if not date_str or not date_str.strip() or date_str.lower() in ['n/a', 'tbd']:
        return "N/A"
    
    date_match = re.search(r"(\d{1,2}/\d{1,2}/\d{2,4})", date_str, re.IGNORECASE)
    if not date_match:
        logger.warning(f"[NASC Date Parser] No standard date pattern (mm/dd/yy) found in '{date_str}'.")
        return date_str

    date_to_parse = date_match.group(1)
    try:
        dt_obj = datetime.strptime(date_to_parse, "%m/%d/%y")
        if dt_obj.year > datetime.now().year + 50: 
            dt_obj = dt_obj.replace(year=dt_obj.year - 100)
    except ValueError:
        try:
            dt_obj = datetime.strptime(date_to_parse, "%m/%d/%Y")
        except ValueError:
            logger.warning(f"[NASC Date Parser] Could not parse extracted date string '{date_to_parse}' from original '{date_str}'.")
            return date_to_parse
            
    return dt_obj.strftime("%Y-%m-%d")


def _fetch_nasc_detail_page_data(url: str, headers_to_use: dict):
    """Fetches and parses the detail page for an opportunity."""
    module_name = "NASC Solutions Detail Fetch"
    logger.debug(f"[{module_name}] Fetching details from: {url}")
    description_text = "Error fetching description."
    due_date_str = "N/A"
    is_closed_or_awarded_flag = False
    status_comment_str = "Status Unknown"

    try:
        time.sleep(REQUEST_DELAY_SECONDS)
        response = requests.get(url, headers=headers_to_use, timeout=25)
        response.raise_for_status()
        detail_soup = BeautifulSoup(response.content, "html.parser")
        
        main_desc_column = detail_soup.select_one("div.et_pb_column_3_5 div.et_pb_post_content")
        if main_desc_column and isinstance(main_desc_column, Tag):
            description_text = main_desc_column.get_text(separator=" ", strip=True)

        progress_heading_el = detail_soup.find(lambda tag: isinstance(tag, Tag) and tag.name == 'h3' and "progress:" in tag.get_text(strip=True).lower())
        if progress_heading_el:
            parent_module = progress_heading_el.find_parent(class_="et_pb_module")
            if parent_module and isinstance(parent_module, Tag):
                date_info_module = parent_module.find_next_sibling(class_="et_pb_module")
                if date_info_module and isinstance(date_info_module, Tag):
                    date_info_div_inner = date_info_module.select_one("div.et_pb_text_inner")
                    if date_info_div_inner:
                        full_progress_text = date_info_div_inner.get_text(strip=True, separator='\n')
                        for line in full_progress_text.splitlines():
                            line_lower = line.lower()
                            if "white papers due" in line_lower:
                                due_date_str = _parse_nasc_date(line)
                            if any(keyword in line_lower for keyword in ["awarded", "solution selected", "closed"]):
                                is_closed_or_awarded_flag = True
                                status_comment_str = f"Status: {line}"
                                break

    except requests.exceptions.RequestException as e:
        logger.error(f"[{module_name}] Error fetching detail page {url}: {e}")
        status_comment_str = "Detail page fetch error"
    except Exception as e_detail:
        logger.error(f"[{module_name}] Error parsing detail page {url}: {e_detail}", exc_info=False)
        status_comment_str = "Detail page parse error"
        
    return description_text, due_date_str, is_closed_or_awarded_flag, status_comment_str

def fetch_nasc_opportunities(headers_to_use: Optional[dict] = None, max_cards_to_process: Optional[int] = None) -> list:
    """
    Fetches all 'Current' opportunities from NASC Solutions.
    """
    module_name = "NASC Solutions" 
    logger.info(f"[{module_name}] 🧪 Starting scraper for: {NASC_OPPORTUNITIES_URL}")
    
    current_headers = headers_to_use if headers_to_use else MODULE_DEFAULT_HEADERS
    results = []
    processed_eligible_cards_count = 0
    
    try:
        response = requests.get(NASC_OPPORTUNITIES_URL, headers=current_headers, timeout=30)
        response.raise_for_status()
        logger.info(f"[{module_name}] Successfully fetched the main opportunities page.")
        soup = BeautifulSoup(response.content, "html.parser")
        portfolio_items_container = soup.select_one("div.et_pb_portfolio_items")
        
        if not portfolio_items_container or not isinstance(portfolio_items_container, Tag):
            logger.warning(f"[{module_name}] Could not find opportunities container 'div.et_pb_portfolio_items'.")
            return results

        opportunity_cards = portfolio_items_container.find_all("div", class_="et_pb_portfolio_item")
        logger.info(f"[{module_name}] Found {len(opportunity_cards)} potential opportunity cards on listing page.")

        for idx, card in enumerate(opportunity_cards):
            if not isinstance(card, Tag): continue

            category_link = card.select_one("p.post-meta a[rel='tag']")
            card_category_text = category_link.get_text(strip=True) if category_link else ""
            
            if "current" not in card_category_text.lower():
                continue

            if max_cards_to_process is not None and processed_eligible_cards_count >= max_cards_to_process:
                logger.info(f"[{module_name}] Reached max_cards_to_process limit ({max_cards_to_process}) for 'Current' items. Stopping.")
                break
            
            title, url = "N/A", "N/A"
            title_header_el = card.select_one("h2.et_pb_module_header a")
            
            if title_header_el:
                title = title_header_el.get_text(strip=True)
                href = str(title_header_el.get('href', ''))
                if href:
                    url = urljoin(NASC_OPPORTUNITIES_URL, href)
            
            if url == "N/A":
                continue

            logger.info(f"[{module_name}] Processing 'Current' card {idx + 1}: '{title}'")
            processed_eligible_cards_count += 1

            detailed_description, close_date, is_closed_or_awarded, status_comment = \
                _fetch_nasc_detail_page_data(url, current_headers)
            
            if is_closed_or_awarded:
                logger.info(f"[{module_name}] Skipping '{title}' due to status: {status_comment}")
                continue

            try:
                if close_date and close_date != "N/A" and datetime.strptime(close_date, "%Y-%m-%d") < datetime.now():
                    logger.info(f"[{module_name}] Skipping '{title}' due to past close date: {close_date}")
                    continue
            except ValueError:
                pass

            logger.info(f"✅ [{module_name}] Scraping '{title}'")
            results.append({
                "Source": module_name, "Title": title,
                "Description": detailed_description, "URL": url,
                "ScrapedDate": datetime.now().isoformat(),
                "Close Date": close_date,
            })

    except requests.exceptions.RequestException as e_req:
        logger.error(f"[{module_name}] Request error: {e_req}")
    except Exception as e:
        logger.error(f"[{module_name}] Unexpected error: {e}", exc_info=True)

    logger.info(f"[{module_name}] Scraper finished. Found {len(results)} total 'Current' opportunities to be analyzed.")
    return results

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(funcName)s - %(message)s', force=True)
    
    logger.info("🚀 Running nasc_solutions_module.py standalone for testing...")
    
    scraped_data = fetch_nasc_opportunities(
        headers_to_use=MODULE_DEFAULT_HEADERS, 
        max_cards_to_process=10
    )
    
    if scraped_data:
        logger.info(f"\n--- {len(scraped_data)} NASC Opportunities Found (Standalone Test) ---")
        print(json.dumps(scraped_data, indent=2))
    else:
        logger.info("No 'Current' NASC opportunities found.")
        
    logger.info("🏁 Standalone test for nasc_solutions_module.py finished.")

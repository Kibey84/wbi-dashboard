# osti_foa_module.py

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
    _handler_osti = logging.StreamHandler()
    _formatter_osti = logging.Formatter('%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(funcName)s - %(message)s')
    _handler_osti.setFormatter(_formatter_osti)
    logger.addHandler(_handler_osti)
    logger.setLevel(logging.INFO)
    logger.propagate = False

# --- Module Configuration ---
OSTI_FOA_URL_MODULE = "https://science.osti.gov/grants/FOAs/Open"
MODULE_DEFAULT_HEADERS_OSTI = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 OpportunityScraperModule/1.0"
}

def _get_detail_text_osti_module(element: Tag, class_name: str, label_text: str) -> str:
    """
    Safely extracts text from a detail element by finding a label and returning the subsequent text.
    """
    div = element.find("div", class_=class_name)
    if not (div and isinstance(div, Tag)):
        return "N/A"

    full_text = div.get_text(strip=True)
    if full_text.lower().startswith(label_text.lower()):
        return full_text[len(label_text):].lstrip(': ').strip()
    return full_text

def _parse_osti_date(date_text_str: str, module_name_for_log="OSTI FOA") -> str:
    """
    Parses various date string formats and returns a standardized 'YYYY-MM-DD' string or 'N/A'.
    """
    if not date_text_str or "n/a" in date_text_str.lower():
        return "N/A"

    cleaned_text = date_text_str.strip()
    date_pattern = r"((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}(?:,)?\s*\d{4}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\d{4}-\d{2}-\d{2})"
    match = re.search(date_pattern, cleaned_text, re.IGNORECASE)

    if not match:
        if "tbd" in cleaned_text.lower(): return "N/A"
        logger.warning(f"[{module_name_for_log}] No standard date pattern found in: '{cleaned_text}'")
        return "N/A (Unrecognized)"

    string_to_parse = (match.group(1) or "").replace(',', '')
    string_to_parse = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", string_to_parse, flags=re.IGNORECASE)

    for fmt in ("%B %d %Y", "%b %d %Y", "%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%m.%d.%Y", "%m/%d/%y", "%m-%d-%y", "%m.%d.%y"):
        try:
            dt_obj = datetime.strptime(string_to_parse, fmt)
            return dt_obj.strftime("%Y-%m-%d")
        except ValueError:
            continue
    
    logger.error(f"[{module_name_for_log}] Failed to parse extracted date: '{string_to_parse}'")
    return "N/A (Parsing Failed)"

def fetch_osti_foas(headers_to_use: Optional[dict[str, str]] = None, max_items: Optional[int] = None) -> list:
    """
    Fetches and parses Funding Opportunity Announcements (FOAs) from the OSTI website.
    This version scrapes all opportunities without keyword filtering.
    """
    module_name = "OSTI FOA"
    logger.info(f"[{module_name}] Starting scraper for: {OSTI_FOA_URL_MODULE}")

    current_headers = headers_to_use or MODULE_DEFAULT_HEADERS_OSTI
    results = []

    try:
        response = requests.get(OSTI_FOA_URL_MODULE, headers=current_headers, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")

        foa_container = soup.find("div", id="dnn_ctr1025_ModuleContent")
        if not (foa_container and isinstance(foa_container, Tag)):
            logger.error(f"[{module_name}] Main FOA container not found. Site structure may have changed.")
            return []

        opportunity_blocks = foa_container.find_all("div", class_="article_content")
        logger.info(f"[{module_name}] Found {len(opportunity_blocks)} potential opportunity blocks.")

        for idx, block in enumerate(opportunity_blocks):
            if max_items is not None and len(results) >= max_items:
                logger.info(f"[{module_name}] Reached max_items limit of {max_items}.")
                break
            
            if not isinstance(block, Tag): continue

            title_el = block.select_one("h3.title a")
            if not (title_el and isinstance(title_el, Tag) and title_el.has_attr("href")):
                logger.debug(f"[{module_name}] Block {idx+1} is not a standard FOA. Skipping.")
                continue

            title = title_el.get_text(strip=True)
            href = str(title_el.get("href", ""))
            url = urljoin(OSTI_FOA_URL_MODULE, href)
            
            notes_el = block.select_one("div.funding_notes")
            description = notes_el.get_text(separator=" ", strip=True) if notes_el and isinstance(notes_el, Tag) else "N/A"
            
            raw_close_date_text = _get_detail_text_osti_module(block, "funding_closedate", "Close Date:")
            parsed_close_date = _parse_osti_date(raw_close_date_text, module_name)
            
            logger.info(f"[{module_name}] Scraping '{title}'")
            results.append({
                "Source": module_name, "Title": title, "Description": description, "URL": url,
                "Close Date": parsed_close_date, "ScrapedDate": datetime.now().isoformat()
            })
            
            time.sleep(0.1)

    except requests.exceptions.RequestException as e_req:
        logger.error(f"[{module_name}] Request error: {e_req}")
    except Exception as e:
        logger.error(f"[{module_name}] Unexpected error: {e}", exc_info=True)

    logger.info(f"[{module_name}] Scraper finished. Found {len(results)} total opportunities to be analyzed.")
    return results

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(funcName)s - %(message)s')
    logger.info("🚀 Running osti_foa_module.py standalone for testing...")
    
    scraped_data = fetch_osti_foas(max_items=5)
    
    if scraped_data:
        print(f"\n--- {len(scraped_data)} OSTI FOAs Found ---")
        print(json.dumps(scraped_data, indent=2))
    else:
        logger.info("No OSTI FOAs were found.")
        
    logger.info("🏁 Standalone test finished.")

import httpx
from bs4 import BeautifulSoup, Tag
from datetime import datetime
import logging
import re
import time
import json
from urllib.parse import urljoin
from typing import Optional, Dict, List, Any

# --- Module-level logger setup ---
logger = logging.getLogger(__name__)

# --- Configuration ---
MODULE_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
}

# --- Field Selectors for robust parsing ---
FIELD_ALIASES = {
    "title": ["h1#opportunity-title", "h1.pageheader span", "h1.title"],
    "description": ["div.contentbody", "div#opportunityDetailView", "#main-content", "article.node-article"],
    "due_date_label": [re.compile(r"(Application Due Date|Expiration Date)", re.I)]
}

# --- Helper Functions ---
def _find_element(soup: BeautifulSoup, aliases: List[Any]) -> Optional[Tag]:
    """Tries a list of selectors/regex patterns to find an element."""
    for alias in aliases:
        if isinstance(alias, str): 
            element = soup.select_one(alias)
            if isinstance(element, Tag):
                return element
        elif isinstance(alias, re.Pattern):  
            element = soup.find(string=alias)
            if element and element.parent and isinstance(element.parent, Tag):
                return element.parent
    return None

def _parse_nih_date(date_str: str) -> str:
    """Parses common date formats found on the NIH website."""
    if not date_str:
        return "N/A"
    date_match = re.search(r"(\w+\s+\d{1,2},\s*\d{4})", date_str)
    if not date_match:
        return date_str.strip()
    
    date_to_parse = date_match.group(1).replace(',', '')
    for fmt in ("%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(date_to_parse, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return date_to_parse

def fetch_nih_detail_page(client: httpx.Client, detail_url: str, title_hint: str) -> Optional[Dict]:
    """Fetches and parses a single opportunity detail page with retry logic."""
    try:
        response = client.get(detail_url)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")

        title_el = _find_element(soup, FIELD_ALIASES["title"])
        final_title = title_el.get_text(strip=True) if title_el else title_hint

        due_date = "N/A"
        due_date_label_el = _find_element(soup, FIELD_ALIASES["due_date_label"])
        if due_date_label_el:
            next_el = due_date_label_el.find_next()
            if next_el:
                due_date = _parse_nih_date(next_el.get_text(strip=True))

        if due_date != "N/A":
            try:
                if datetime.strptime(due_date, "%Y-%m-%d") < datetime.now():
                    logger.info(f"[NIH SBIR] Skipping expired opportunity: {final_title}")
                    return None
            except ValueError:
                pass 
        desc_el = _find_element(soup, FIELD_ALIASES["description"])
        description = desc_el.get_text(separator=' ', strip=True) if desc_el else "Description not found."

        return {
            "Source": "NIH SBIR",
            "Title": final_title,
            "Description": description[:3500],
            "URL": detail_url,
            "ScrapedDate": datetime.now().isoformat(),
            "Close Date": due_date
        }

    except Exception as e:
        logger.error(f"[NIH SBIR] Error fetching detail page {detail_url}: {e}", exc_info=True)
        return None

def fetch_nih_sbir_opportunities(headers_to_use: Optional[Dict[str, str]] = None, max_items: Optional[int] = None) -> List[Dict]:
    logger.info("[NIH SBIR] Starting fetch of NIH opportunities...")
    search_url = "https://grants.nih.gov/grants/guide/index.html"
    headers = headers_to_use or MODULE_DEFAULT_HEADERS
    results = []
    max_retries = 3

    for attempt in range(max_retries):
        try:
            with httpx.Client(headers=headers, follow_redirects=True, timeout=30.0) as client:
                response = client.get(search_url)
                response.raise_for_status()
                soup = BeautifulSoup(response.content, "html.parser")

                links = soup.select("div#main-content a[href]")
                logger.info(f"[NIH SBIR] Found {len(links)} links to examine.")

                for link in links:
                    if max_items is not None and len(results) >= max_items:
                        break
                    
                    href_val = link.get("href")
                    if not href_val or not isinstance(href_val, str):
                        continue
                    
                    href_str = urljoin(search_url, href_val.strip())
                    title_hint = link.get_text(strip=True)

                    if "grants.nih.gov/grants/guide/pa-files/" in href_str or "grants.nih.gov/grants/guide/rfa-files/" in href_str:
                        detail_data = fetch_nih_detail_page(client, href_str, title_hint)
                        if detail_data:
                            results.append(detail_data)
                            logger.info(f"✅ Scraped NIH opportunity: {detail_data['Title']}")
                        time.sleep(0.2) 

                logger.info(f"[NIH SBIR] Finished. Found {len(results)} active opportunities.")
                return results

        except httpx.RequestError as e:
            logger.error(f"[NIH SBIR] Attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            else:
                logger.error("[NIH SBIR] All retries failed for main page.")
        except Exception as e:
            logger.error(f"[NIH SBIR] An unexpected error occurred: {e}", exc_info=True)
            break

    return results

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    test_opps = fetch_nih_sbir_opportunities(max_items=5)
    print(f"\n--- Found {len(test_opps)} NIH SBIR Opportunities ---")
    print(json.dumps(test_opps, indent=2))

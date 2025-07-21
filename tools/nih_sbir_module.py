import requests
from bs4 import BeautifulSoup, Tag
from datetime import datetime
import logging
import re
import time
import json
from urllib.parse import urljoin
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

MODULE_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
}

def _parse_nih_date(date_str: str) -> str:
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

def fetch_nih_detail_page(detail_url: str, title_hint: str) -> Optional[Dict]:
    try:
        response = requests.get(detail_url, headers=MODULE_DEFAULT_HEADERS, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")

        title_el = soup.select_one("h1#opportunity-title, h1.pageheader span")
        final_title = title_el.get_text(strip=True) if isinstance(title_el, Tag) else title_hint

        due_date = "N/A"
        due_date_el = soup.find(string=re.compile(r"(Application Due Date|Expiration Date)", re.I))
        if due_date_el is not None:
            next_sibling = due_date_el.find_next() if hasattr(due_date_el, 'find_next') else None
            if next_sibling is not None:
                next_text = next_sibling.get_text(strip=True) if isinstance(next_sibling, Tag) else str(next_sibling).strip()
                due_date = _parse_nih_date(next_text)

        if due_date != "N/A":
            try:
                if datetime.strptime(due_date, "%Y-%m-%d") < datetime.now():
                    logger.info(f"[NIH SBIR] Skipping expired opportunity: {final_title}")
                    return None
            except Exception:
                pass

        desc_el = soup.select_one("div.contentbody, div#opportunityDetailView, #main-content")
        description = desc_el.get_text(separator=' ', strip=True) if isinstance(desc_el, Tag) else "Description not found."

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

    try:
        response = requests.get(search_url, headers=headers, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")

        links = soup.select("div#main-content a")
        logger.info(f"[NIH SBIR] Found {len(links)} links to examine.")

        for link in links:
            if max_items is not None and len(results) >= max_items:
                break
            href_val = link.get("href")
            if not href_val:
                continue
            href_str = str(href_val).strip()
            if "grants.nih.gov" not in href_str:
                href_str = urljoin(search_url, href_str)
            title_hint = link.get_text(strip=True)
            detail_data = fetch_nih_detail_page(href_str, title_hint)
            if detail_data:
                results.append(detail_data)

    except Exception as e:
        logger.error(f"[NIH SBIR] Error fetching NIH main page: {e}", exc_info=True)

    logger.info(f"[NIH SBIR] Finished. Found {len(results)} active opportunities.")
    return results

if __name__ == "__main__":
    test_opps = fetch_nih_sbir_opportunities(max_items=5)
    print(f"\n--- Found {len(test_opps)} NIH SBIR Opportunities ---")
    print(json.dumps(test_opps, indent=2))

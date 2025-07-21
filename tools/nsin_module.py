import logging
import requests
import time
from bs4 import BeautifulSoup, Tag
from urllib.parse import urljoin
from datetime import datetime
import re
import json
from typing import Optional, List, Dict

# Setup logger
logger = logging.getLogger(__name__)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    _handler.setFormatter(_formatter)
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)

MODULE_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

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

def _parse_nsin_detail_page(detail_url: str, headers_to_use: dict) -> tuple[str, str, Optional[datetime], Optional[datetime]]:
    title, description, open_date_obj, closing_date_obj = "Title Not Found", "Description Not Found", None, None
    try:
        resp = requests.get(detail_url, headers=headers_to_use, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        title_el = soup.select_one("h1.entry-title, h1.page-title, h1.title")
        if isinstance(title_el, Tag):
            title = title_el.get_text(strip=True)
        elif soup.title and soup.title.string:
            title = soup.title.string.strip()

        desc_el = soup.select_one("div.entry-content, article.content, main#main, div.post-content")
        if desc_el and isinstance(desc_el, Tag):
            key_heading = next(
                (h for h in desc_el.find_all(re.compile("^h[1-6]")) if "key dates" in h.get_text(strip=True).lower()),
                None
            )
            if key_heading and isinstance(key_heading, Tag):
                sibling = key_heading.find_next_sibling(['ul', 'div']) or key_heading.parent
                if sibling and isinstance(sibling, Tag):
                    for tag in sibling.find_all(['li', 'p']):
                        line = tag.get_text(strip=True)
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

def fetch_nsin_opportunities(headers: dict = MODULE_DEFAULT_HEADERS) -> List[Dict]:
    logger.info("[NSIN] 🔍 Scraping NSIN event opportunities (requests-based)...")
    base_url = "https://www.nsin.mil/events/"
    results = []

    try:
        resp = requests.get(base_url, headers=headers, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

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

                title, desc, open_date, close_date = _parse_nsin_detail_page(detail_url, headers)
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

                time.sleep(0.5)

    except Exception as e:
        logger.error(f"[NSIN] ❌ Failed to scrape NSIN: {e}", exc_info=True)

    logger.info(f"[NSIN] Done. {len(results)} opportunities collected.")
    return results

if __name__ == "__main__":
    data = fetch_nsin_opportunities()
    print(f"\n--- Scraped {len(data)} NSIN Opportunities ---")
    print(json.dumps(data, indent=2))

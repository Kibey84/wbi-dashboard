import time
import logging
import requests
from bs4 import BeautifulSoup, Tag
from urllib.parse import urljoin
from datetime import datetime
import re
import json
from typing import Optional

# Logger setup
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

MODULE_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
}

def _parse_nstxl_date(date_text: str) -> str:
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

def fetch_nstxl_detail_page(detail_url: str, listing_title: str) -> Optional[dict]:
    try:
        response = requests.get(detail_url, headers=MODULE_DEFAULT_HEADERS, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")

        title = soup.select_one("h1.entry-title, h1.fusion-post-title")
        final_title = title.get_text(strip=True) if title else listing_title

        desc_el = soup.select_one("div.post-content, div.entry-content")
        if desc_el:
            for unwanted in desc_el.select('script, style, form, button, .related-posts'):
                unwanted.decompose()
            description = ' '.join(desc_el.get_text(separator=" ", strip=True).split())[:3500]
        else:
            description = "Description not found."

        close_date = "N/A"
        candidates = soup.find_all(string=re.compile(r"(proposals|responses|offers)\s+due|closing\s+date|submission\s+deadline", re.I))
        if candidates:
            parent = candidates[0].find_parent(['p', 'h1', 'h2', 'h3', 'h4', 'div']) if hasattr(candidates[0], 'find_parent') else None
            if parent:
                close_date = _parse_nstxl_date(parent.get_text())

        if close_date == "N/A":
            h6 = soup.select_one("h6.fusion-title-heading")
            if h6:
                close_date = _parse_nstxl_date(h6.get_text())

        if close_date != "N/A":
            try:
                if datetime.strptime(close_date, "%Y-%m-%d") < datetime.now():
                    logger.info(f"Skipping (Past Close Date): {final_title}")
                    return None
            except ValueError:
                logger.warning(f"Could not parse date '{close_date}' for filtering.")

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
        logger.error(f"Error fetching detail page {detail_url}: {e}")
        return None

def fetch_nstxl_opportunities() -> list:
    base_url = "https://nstxl.org/opportunities/"
    results = []
    try:
        response = requests.get(base_url, headers=MODULE_DEFAULT_HEADERS, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")

        links = soup.select("h2.entry-title.fusion-post-title > a[href]")
        opportunities = [{
            "url": urljoin(base_url, str(link.get('href')).strip()),
            "listing_title": link.get_text(strip=True)
        } for link in links if link.get('href')]

        logger.info(f"Found {len(opportunities)} opportunities on listing page.")

        for idx, opp in enumerate(opportunities[:25]):
            logger.info(f"Processing {idx+1}/{len(opportunities)}: {opp['url']}")
            detail = fetch_nstxl_detail_page(opp['url'], opp['listing_title'])
            if detail:
                results.append(detail)
            time.sleep(1)

    except Exception as e:
        logger.error(f"Error fetching NSTXL listing: {e}")

    logger.info(f"Scraping complete. {len(results)} valid opportunities found.")
    return results

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', force=True)
    logger.info("🚀 Starting NSTXL scraper standalone...")

    scraped = fetch_nstxl_opportunities()
    if scraped:
        print(json.dumps(scraped, indent=2))
    else:
        logger.info("No opportunities found.")

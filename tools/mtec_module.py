import requests
from bs4 import BeautifulSoup, Tag
from datetime import datetime
import logging
import re

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

MODULE_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
}

def _parse_mtec_date(date_str: str) -> str:
    if not date_str or not date_str.strip() or date_str.lower() in ['n/a', 'tbd']:
        return "N/A"

    date_match = re.search(r"((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},\s*\d{4}|\d{1,2}/\d{1,2}/\d{2,4})", date_str, re.IGNORECASE)
    if not date_match:
        return date_str

    date_to_parse = date_match.group(1).replace(',', '')
    for fmt in ("%B %d %Y", "%b %d %Y", "%m/%d/%Y", "%m/%d/%y"):
        try:
            dt_obj = datetime.strptime(date_to_parse, fmt)
            if '%y' in fmt and dt_obj.year > datetime.now().year + 20:
                dt_obj = dt_obj.replace(year=dt_obj.year - 100)
            return dt_obj.strftime("%Y-%m-%d")
        except ValueError:
            continue

    return date_to_parse

def _get_details_from_page(detail_url: str, headers: dict) -> tuple:
    title, description, open_date, close_date = "Title Not Found", "Description Not Found", "N/A", "N/A"
    try:
        response = requests.get(detail_url, headers=headers, timeout=30)
        response.raise_for_status()
        psoup = BeautifulSoup(response.text, "html.parser")

        title_el = psoup.select_one("h1.entry-title, h1.page-title, h1.title")
        title = title_el.get_text(strip=True) if isinstance(title_el, Tag) else (
            psoup.title.string.strip() if psoup.title and psoup.title.string else "Title Not Found"
        )

        desc_el = psoup.select_one("div.entry-content, article.content, main#main, div.post-content")
        if isinstance(desc_el, Tag):
            key_dates_heading = desc_el.find(
                lambda tag: isinstance(tag, Tag)
                and bool(tag.name)
                and tag.name.startswith('h')
                and "key dates" in tag.get_text(strip=True).lower()
        )
            if key_dates_heading:
                element_container = key_dates_heading.find_next_sibling(['ul', 'div', 'table']) or key_dates_heading.parent
                if isinstance(element_container, Tag):
                    closing_date_labels = ["responses due", "proposals due", "applications close", "proposals close", "closing date", "submission deadline", "closes"]
                    open_date_labels = ["issue date", "release date", "open", "begins", "launches"]

                    for item_tag in element_container.find_all(['li', 'p', 'tr']):
                        item_text = item_tag.get_text(strip=True)
                        item_text_lower = item_text.lower()
                        if close_date == "N/A" and any(label in item_text_lower for label in closing_date_labels):
                            close_date = _parse_mtec_date(item_text)
                        if open_date == "N/A" and any(label in item_text_lower for label in open_date_labels):
                            open_date = _parse_mtec_date(item_text)
            for unwanted in desc_el.select('div.social-share-group'):
                unwanted.decompose()
            description = ' '.join(desc_el.get_text(separator=" ", strip=True).split())[:3500]

    except Exception as e:
        logger.error(f"[MTEC Detail] Error parsing {detail_url}: {e}", exc_info=False)

    return title, description, open_date, close_date

def fetch_mtec_opportunities():
    url = "https://www.mtec-sc.org/solicitations/"
    results = []
    try:
        response = requests.get(url, headers=MODULE_DEFAULT_HEADERS, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        posts = soup.select("div.post-content")
        logger.info(f"Found {len(posts)} MTEC posts.")

        for post in posts:
            title_el = post.select_one("h3 a")
            title = title_el.get_text(strip=True) if isinstance(title_el, Tag) else "No Title Found"
            link = str(title_el['href']).strip() if isinstance(title_el, Tag) and title_el.has_attr('href') else None

            if not link:
                continue

            fetched_title, description, open_date, close_date = _get_details_from_page(link, MODULE_DEFAULT_HEADERS)

            if close_date != "N/A" and len(close_date) == 10:
                try:
                    if datetime.strptime(close_date, "%Y-%m-%d") < datetime.now():
                        logger.info(f"Skipping '{fetched_title[:60]}' — closing date {close_date} passed.")
                        continue
                except ValueError:
                    pass

            results.append({
                "Source": "MTEC",
                "Title": fetched_title,
                "Description": description,
                "URL": link,
                "ScrapedDate": datetime.now().isoformat(),
                "Open Date": open_date,
                "Close Date": close_date
            })

    except Exception as e:
        logger.error(f"Failed to fetch MTEC opportunities: {e}")

    return results

if __name__ == "__main__":
    scraped_data = fetch_mtec_opportunities()
    print(f"\n--- Scraped {len(scraped_data)} MTEC Opportunities ---")
    for item in scraped_data:
        print(item)

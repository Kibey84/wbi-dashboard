import requests
from bs4 import BeautifulSoup, Tag
from datetime import datetime
import logging
import re
from typing import cast

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

DEFAULT_HEADERS_NASA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
}

def _parse_nasa_date(date_str: str) -> str:
    if not date_str or date_str.strip() in ["—", "-", ""]:
        return "N/A"

    date_match = re.match(r"(\\d{1,2}/\\d{1,2}/\\d{2,4}|\\w+\\s+\\d{1,2}(?:st|nd|rd|th)?(?:,)?\\s*\\d{4}|\\d{4}-\\d{1,2}-\\d{1,2})", date_str.strip(), re.IGNORECASE)
    date_clean = date_match.group(1) if date_match else date_str.strip()

    formats = ["%B %d, %Y", "%b %d, %Y", "%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"]
    date_clean = date_clean.replace(",", "").replace("st", "").replace("nd", "").replace("rd", "").replace("th", "")

    for fmt in formats:
        try:
            dt = datetime.strptime(date_clean, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return "N/A"

def fetch_nasa_sbir_opportunities():
    url = "https://www.nasa.gov/sbir-sttr-program/solicitations-and-opportunities/"
    results = []
    try:
        response = requests.get(url, headers=DEFAULT_HEADERS_NASA, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        rows = soup.select("figure.wp-block-table table tbody tr")
        logger.info(f"Found {len(rows)} rows in NASA SBIR table.")

        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue

            name = cells[0].get_text(strip=True)

            cell = cast(Tag, cells[0])
            link_el = cell.find("a")

            link = None
            if isinstance(link_el, Tag) and link_el.has_attr("href"):
                link = str(link_el["href"]).strip()

            open_date = _parse_nasa_date(cells[1].get_text(strip=True))
            close_date = _parse_nasa_date(cells[2].get_text(strip=True))
   
            if open_date != "N/A" and close_date != "N/A":
                try:
                    if datetime.strptime(close_date, "%Y-%m-%d") < datetime.now():
                        logger.info(f"Skipping expired opportunity: {name}")
                        continue
                except ValueError:
                    pass

            if not link:
                continue

            logger.info(f"✅ Scraping opportunity: {name}")
            detail_desc = fetch_detail_page_description(link)

            results.append({
                "Source": "NASA SBIR/STTR",
                "Title": name,
                "Description": detail_desc,
                "URL": link,
                "Open Date": open_date,
                "Close Date": close_date,
                "ScrapedDate": datetime.now().isoformat()
            })

    except Exception as e:
        logger.error(f"Failed to fetch NASA SBIR opportunities: {e}", exc_info=True)

    return results

def fetch_detail_page_description(detail_url: str) -> str:
    try:
        response = requests.get(detail_url, headers=DEFAULT_HEADERS_NASA, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        desc_el = soup.select_one("div.entry-content, main#main-content, article.content")
        if desc_el:
            for unwanted in desc_el.select("header, footer, nav, aside"):
                unwanted.decompose()
            return ' '.join(desc_el.get_text(separator=" ", strip=True).split())[:3500]
    except Exception as e:
        logger.warning(f"Failed to fetch detail page {detail_url}: {e}")
    return "Description not found."

if __name__ == "__main__":
    data = fetch_nasa_sbir_opportunities()
    print(f"\n--- Scraped {len(data)} NASA SBIR/STTR Opportunities ---")
    for item in data:
        print(item)

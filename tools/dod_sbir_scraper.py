import requests
from bs4 import BeautifulSoup, Tag
from datetime import datetime
import re
import logging

logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

def parse_dod_date(text):
    if not text:
        return "N/A"

    patterns = [
        r"\b([A-Za-z]+ \d{1,2}, \d{4})\b",
        r"\b(\d{1,2}/\d{1,2}/\d{4})\b",
        r"\b(\d{4}-\d{1,2}-\d{1,2})\b"
    ]
    for pat in patterns:
        match = re.search(pat, text)
        if match:
            try:
                dt = datetime.strptime(match.group(1), "%B %d, %Y")
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                try:
                    dt = datetime.strptime(match.group(1), "%m/%d/%Y")
                    return dt.strftime("%Y-%m-%d")
                except ValueError:
                    try:
                        dt = datetime.strptime(match.group(1), "%Y-%m-%d")
                        return dt.strftime("%Y-%m-%d")
                    except ValueError:
                        return match.group(1)
    return "N/A"

def fetch_dod_sbir_sttr_topics():
    url = "https://www.dodsbirsttr.mil/topics-app/"
    logger.info(f"Fetching DoD SBIR/STTR topics from {url}")
    opportunities = []

    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        topic_sections = soup.select("div.accordion-padding mat-expansion-panel")

        logger.info(f"Found {len(topic_sections)} topic sections.")

        for idx, panel in enumerate(topic_sections, 1):
            try:
                header = panel.select_one("mat-panel-title")
                if not header or not header.get_text(strip=True):
                    continue

                title_raw = header.get_text(strip=True)
                if "Open" not in title_raw:
                    continue

                description_div = panel.select_one("div.mat-expansion-panel-content")
                description = description_div.get_text(separator=" ", strip=True) if description_div else "No description found."

                topic_id_match = re.match(r"([A-Z]+\d+[\w\.-]*)", title_raw)
                topic_id = topic_id_match.group(1) if topic_id_match else f"UNKNOWN_{idx}"

                clean_title = re.sub(r"([A-Z]+\d+[\w\.-]*)", "", title_raw).strip(" -:")

                full_url = f"{url}#/topic/{topic_id}"
                close_date = parse_dod_date(title_raw)

                opportunities.append({
                    "Source": "DoD SBIR/STTR",
                    "Title": clean_title,
                    "Description": description,
                    "URL": full_url,
                    "Close Date": close_date,
                    "ScrapedDate": datetime.now().strftime("%Y-%m-%d")
                })

                logger.info(f"✅ Scraped DoD SBIR Topic: {clean_title}")

            except Exception as e:
                logger.warning(f"Error parsing topic {idx}: {e}")

    except Exception as e:
        logger.error(f"Failed to fetch DoD SBIR/STTR topics: {e}", exc_info=True)

    return opportunities

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    scraped = fetch_dod_sbir_sttr_topics()
    print(f"\n--- Scraped {len(scraped)} DoD SBIR/STTR Opportunities ---")
    for opp in scraped:
        print(opp)

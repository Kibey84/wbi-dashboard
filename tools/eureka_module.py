import requests
from bs4 import BeautifulSoup, Tag
from urllib.parse import urljoin
from datetime import datetime
import logging
import re

logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

def fetch_eureka_opportunities():
    url = "https://eurekanetwork.org/opencalls/"
    logger.info(f"Fetching EUREKA opportunities from {url}")
    opportunities = []

    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        card_links = soup.select("div.bg-white.group > a[href]")

        logger.info(f"Found {len(card_links)} open call links.")

        for idx, card_link in enumerate(card_links, 1):
            try:
                href = str(card_link.get("href", "")).strip()
                if href:
                    full_url = urljoin(url, href)

                title_el = card_link.select_one("h3.heading-sm, h2.heading-md")
                title_text = title_el.get_text(strip=True) if title_el else f"Open Call {idx}"

                logger.info(f"Processing detail page: {full_url}")
                detail_resp = requests.get(full_url, timeout=30)
                detail_resp.raise_for_status()
                detail_soup = BeautifulSoup(detail_resp.text, "html.parser")

                # Extract title
                detail_title_el = detail_soup.select_one("h1.heading-xl, h1.font-bold, h2.call-title, h1.text-3xl, header.wp-block-post-title h1")
                final_title = detail_title_el.get_text(strip=True) if detail_title_el else title_text

                # Extract close date
                close_date_str = "N/A"
                date_match = detail_soup.find(lambda tag: isinstance(tag, Tag) and "deadline:" in tag.get_text(strip=True).lower())
                if date_match:
                    date_found = re.search(r"Deadline:\s*(\d{1,2}\s+\w+\s+\d{4})", date_match.get_text(strip=True))
                    if date_found:
                        try:
                            close_date_obj = datetime.strptime(date_found.group(1), "%d %B %Y")
                            if close_date_obj >= datetime.now():
                                close_date_str = close_date_obj.strftime("%Y-%m-%d")
                            else:
                                logger.info(f"Skipping expired opportunity: {final_title}")
                                continue
                        except ValueError:
                            logger.warning(f"Could not parse date: {date_found.group(1)}")

                # Extract description
                desc_el = detail_soup.select_one("div.prose, div.wysiwyg-content, article.post-content, div.entry-content, main#main")
                description = "Description not found."
                if desc_el:
                    for unwanted in desc_el.select("nav, footer, header, aside, .sidebar, form, div[class*='share'], div[class*='related'], div[class*='meta']"):
                        unwanted.decompose()
                    description = ' '.join(desc_el.get_text(separator=" ", strip=True).split())

                opportunities.append({
                    "Source": "EUREKA",
                    "Title": final_title,
                    "Description": description,
                    "URL": full_url,
                    "ScrapedDate": datetime.now().strftime("%Y-%m-%d"),
                    "Close Date": close_date_str
                })

                logger.info(f"✅ Scraped opportunity: {final_title}")

            except Exception as e:
                logger.warning(f"Error processing opportunity {idx}: {e}")

    except Exception as e:
        logger.error(f"Failed to fetch EUREKA page: {e}", exc_info=True)

    return opportunities

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    scraped = fetch_eureka_opportunities()
    print(f"\n--- Scraped {len(scraped)} EUREKA Opportunities ---")
    for opp in scraped:
        print(opp)

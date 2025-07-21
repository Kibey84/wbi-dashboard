import requests
from bs4 import BeautifulSoup, Tag
import logging
from datetime import datetime

def fetch_iarpa_opportunities():
    url = "https://www.iarpa.gov/engage-with-us/open-baas"
    logging.info(f"Fetching IARPA Open BAAs page: {url}")
    opportunities = []

    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        content_area = soup.find(id="dnn_ctr497_View_ScopeWrapper")
        if not isinstance(content_area, Tag):
            logging.warning("IARPA content area not found or wrong type.")
            return []

        baa_blocks = content_area.find_all("article")
        logging.info(f"Found {len(baa_blocks)} BAA blocks.")

        for block in baa_blocks:
            if not isinstance(block, Tag):
                continue

            title_element = block.find("h2")
            title = title_element.get_text(strip=True) if isinstance(title_element, Tag) else "No Title Found"

            link_element = title_element.find("a") if isinstance(title_element, Tag) else None
            link_url = (
                str(link_element["href"]).strip()
                if isinstance(link_element, Tag) and link_element.has_attr("href")
                else "No Link Found"
            )

            desc_element = block.find("p")
            description = desc_element.get_text(strip=True) if isinstance(desc_element, Tag) else "No Description Found"

            opportunities.append({
                "Title": title,
                "Description": description,
                "URL": link_url,
                "Close Date": "See BAA for details",
                "Source": "IARPA",
                "ScrapedDate": datetime.now().strftime("%Y-%m-%d")
            })
            logging.info(f"✅ Scraped IARPA BAA: {title}")

    except Exception as e:
        logging.error(f"Failed to fetch IARPA page: {e}", exc_info=True)

    return opportunities


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    data = fetch_iarpa_opportunities()
    print(f"\n--- Scraped {len(data)} IARPA Opportunities ---")
    for item in data:
        print(item)

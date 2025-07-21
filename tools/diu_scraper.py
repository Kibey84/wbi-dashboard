import requests
from bs4 import BeautifulSoup, Tag
from datetime import datetime
import logging
from bs4 import Tag

def fetch_diu_opportunities():
    url = "https://www.diu.mil/work-with-us/open-solicitations"
    logging.info(f"Fetching DIU opportunities from {url}")
    opportunities = []

    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        accordion_headers = soup.select("div.usa-accordion h4.usa-accordion__heading")
        logging.info(f"Found {len(accordion_headers)} DIU solicitation headers.")

        for header in accordion_headers:
            try:
                title = header.get_text(strip=True)
                # Ensure we only proceed if the next element is a Tag
                next_sibling = header.find_next_sibling()
                if not isinstance(next_sibling, Tag):
                    logging.warning(f"No valid content found for '{title}'. Skipping.")
                    continue

                paragraphs = next_sibling.find_all("p")
                description = "\n".join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))

                # Find the submission link by looping manually
                link_url = "No Submission Link Found"
                for a_tag in next_sibling.find_all("a"):
                    if not isinstance(a_tag, Tag):
                        continue
                    if "Submit a Solution" in a_tag.get_text():
                        if a_tag.has_attr("href"):
                            link_url = a_tag["href"]
                        break

                # Look for the close date in the paragraphs
                close_date_text = "See Description"
                for p in paragraphs:
                    p_text = p.get_text()
                    if "Submissions are due by" in p_text:
                        parts = p_text.split("Submissions are due by")
                        if len(parts) > 1:
                            close_date_text = parts[1].split("at")[0].strip()
                        break

                opportunities.append({
                    "Title": title,
                    "Description": description,
                    "URL": link_url,
                    "Close Date": close_date_text,
                    "ScrapedDate": datetime.now().strftime("%Y-%m-%d")
                })

                logging.info(f"✅ Scraped DIU opportunity: {title}")

            except Exception as e:
                logging.warning(f"Error parsing solicitation '{title}': {e}")

    except Exception as e:
        logging.error(f"Failed to fetch DIU page: {e}", exc_info=True)

    return opportunities

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    scraped = fetch_diu_opportunities()
    print(f"\n--- Scraped {len(scraped)} DIU Opportunities ---")
    for opp in scraped:
        print(opp)

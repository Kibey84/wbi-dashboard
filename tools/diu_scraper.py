import httpx
from bs4 import BeautifulSoup, Tag
from datetime import datetime
import logging
import time
import json

def fetch_diu_opportunities():
    """
    Fetches DIU opportunities using httpx with retry logic to handle instability.
    """
    url = "https://www.diu.mil/work-with-us/open-solicitations"
    logging.info(f"Fetching DIU opportunities from {url}")
    opportunities = []
    max_retries = 3

    for attempt in range(max_retries):
        try:
            with httpx.Client(follow_redirects=True, verify=False, timeout=30.0) as client:
                response = client.get(url)
                response.raise_for_status()
            
            soup = BeautifulSoup(response.text, "html.parser")

            accordion_headers = soup.select("div.usa-accordion h4.usa-accordion__heading")
            logging.info(f"Found {len(accordion_headers)} DIU solicitation headers.")

            for header in accordion_headers:
                title = "Unknown Title"
                try:
                    title = header.get_text(strip=True)
                    next_sibling = header.find_next_sibling()
                    
                    if not isinstance(next_sibling, Tag):
                        logging.warning(f"No valid content found for '{title}'. Skipping.")
                        continue

                    paragraphs = next_sibling.find_all("p")
                    description = "\n".join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))

                    link_url = "No Submission Link Found"
                    for a_tag in next_sibling.find_all("a", href=True):
                        if isinstance(a_tag, Tag) and "Submit a Solution" in a_tag.get_text():
                            link_url = a_tag.get("href", link_url)
                            break

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

            return opportunities

        except httpx.RequestError as e:
            logging.error(f"Attempt {attempt + 1} of {max_retries} failed for DIU page: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  
            else:
                logging.error("All retries failed for DIU. Returning empty list.")
                return []
        except Exception as e:
            logging.error(f"An unexpected error occurred: {e}", exc_info=True)
            return []

    return opportunities

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    scraped = fetch_diu_opportunities()
    print(f"\n--- Scraped {len(scraped)} DIU Opportunities ---")
    for opp in scraped:
        print(json.dumps(opp, indent=2))

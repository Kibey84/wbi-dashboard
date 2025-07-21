import requests
from bs4 import BeautifulSoup
import logging
from datetime import datetime

def fetch_arpae_opportunities(url_to_scrape):
    """
    Scrapes a specific Funding Opportunity Announcement (FOA) from the ARPA-E portal using HTTP requests.
    """
    logging.info(f"Fetching ARPA-E opportunity: {url_to_scrape}")
    opportunities = []

    try:
        response = requests.get(url_to_scrape, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        title = "Title Not Found"
        try:
            title_element = soup.find('h1')
            if title_element:
                title = title_element.get_text(strip=True)
        except Exception as e:
            logging.warning(f"Could not extract title: {e}")

        description = "Description not found."
        try:
            description_elements = soup.select('div.display-field p')
            description_parts = [p.get_text(strip=True) for p in description_elements if p.get_text(strip=True)]
            if description_parts:
                description = "\n".join(description_parts)
        except Exception as e:
            logging.warning(f"Could not extract description: {e}")

        close_date = "Not Found"
        try:
            date_elements = soup.select('div.foa-view-detail-value')
            if len(date_elements) > 1:
                close_date = date_elements[-2].get_text(strip=True)
        except Exception as e:
            logging.warning(f"Could not extract close date: {e}")

        opp = {
            'Title': title,
            'Description': description,
            'URL': url_to_scrape,
            'Close Date': close_date,
            'ScrapedDate': datetime.now().isoformat(),
            'Source': 'ARPA-E'
        }
        opportunities.append(opp)
        logging.info(f"✅ Scraped ARPA-E opportunity: {title[:50]}...")

    except Exception as e:
        logging.error(f"Failed to fetch ARPA-E opportunity at {url_to_scrape}: {e}", exc_info=True)

    return opportunities

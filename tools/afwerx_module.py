import time
import logging
from bs4 import BeautifulSoup
import requests
from urllib.parse import urljoin
from datetime import datetime
import re
import sys

logger = logging.getLogger(__name__)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    _handler.setFormatter(_formatter)
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}


def fetch_afwerx_opportunities() -> list:
    base_url = "https://afwerxchallenge.com/current-efforts/"
    results = []

    try:
        response = requests.get(base_url, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")

        cards = soup.select('div.featured-content-card a[href]')
        for card in cards:
            href = str(card.get("href")).strip()
            if not href:
                continue
            full_url = urljoin(base_url, href)

            # Fetch detail page
            try:
                detail_resp = requests.get(full_url, timeout=30)
                detail_resp.raise_for_status()
                detail_soup = BeautifulSoup(detail_resp.content, "html.parser")

                title_el = detail_soup.select_one('h1.title, h1.challenge-title, div.title-holder h1, h1.entry-title')
                title = title_el.get_text(strip=True) if title_el else "No Title Found"

                desc_el = detail_soup.select_one('div.challenge-description, div.description-content, section.overview-section, div.fr-view, article#main-content')
                description = desc_el.get_text(separator=" ", strip=True) if desc_el else "No Description Found"

                # Extract dates with regex
                open_date_str = "N/A"
                close_date_str = "N/A"
                text_for_date_extraction = description

                open_date_match = re.search(r"opens on\s+(\d{1,2}/\d{1,2}/\d{4})", text_for_date_extraction, re.IGNORECASE)
                if open_date_match:
                    try:
                        open_date_str = datetime.strptime(open_date_match.group(1), "%m/%d/%Y").strftime("%Y-%m-%d")
                    except ValueError:
                        open_date_str = open_date_match.group(1)

                close_date_match = re.search(r"ends on\s+(\d{1,2}/\d{1,2}/\d{4})", text_for_date_extraction, re.IGNORECASE)
                if close_date_match:
                    try:
                        close_date_str = datetime.strptime(close_date_match.group(1), "%m/%d/%Y").strftime("%Y-%m-%d")
                    except ValueError:
                        close_date_str = close_date_match.group(1)

                results.append({
                    "Source": "AFWERX",
                    "Title": title,
                    "Description": description[:1000],  
                    "URL": full_url,
                    "Open Date": open_date_str,
                    "Close Date": close_date_str,
                    "ScrapedDate": datetime.now().isoformat()
                })

            except Exception as detail_err:
                logging.error(f"Error fetching detail page {full_url}: {detail_err}")

    except Exception as e:
        logging.error(f"Error fetching main AFWERX page: {e}")

    logging.info(f"Fetched {len(results)} AFWERX opportunities.")
    return results


def fetch_afwerx_detail(url, listing_title) -> dict | None:
    try:
        response = requests.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        # Extract title
        title_tag = soup.select_one("h1.title, h1.challenge-title, div.title-holder h1, h1.entry-title")
        final_title = title_tag.get_text(strip=True) if title_tag else listing_title

        # Extract description
        desc_tag = soup.select_one("div.challenge-description, div.description-content, section.overview-section, div.fr-view, article#main-content")
        desc = desc_tag.get_text(separator=" ", strip=True) if desc_tag else "No description available"
        desc_cleaned = ' '.join(desc.split())[:1000]

        # Extract dates
        open_date, close_date = extract_dates_from_text(desc)

        return {
            "Source": "AFWERX",
            "Title": final_title,
            "Description": desc_cleaned,
            "URL": url,
            "Open Date": open_date,
            "Close Date": close_date,
            "ScrapedDate": datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f"Error processing detail page {url}: {e}")
        return None


def extract_dates_from_text(text):
    open_date = "N/A"
    close_date = "N/A"

    open_match = re.search(r"opens on\s+(\d{1,2}/\d{1,2}/\d{4})", text, re.IGNORECASE)
    if open_match:
        try:
            open_date = datetime.strptime(open_match.group(1), "%m/%d/%Y").strftime("%Y-%m-%d")
        except ValueError:
            open_date = open_match.group(1)

    close_match = re.search(r"ends on\s+(\d{1,2}/\d{1,2}/\d{4})", text, re.IGNORECASE)
    if close_match:
        try:
            close_date = datetime.strptime(close_match.group(1), "%m/%d/%Y").strftime("%Y-%m-%d")
        except ValueError:
            close_date = close_match.group(1)

    return open_date, close_date


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    opportunities = fetch_afwerx_opportunities()
    print(f"Found {len(opportunities)} AFWERX opportunities.")

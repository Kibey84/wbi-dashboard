import requests
import logging
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from datetime import datetime
import re
import json

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def parse_date_from_text(date_text_str: str) -> str:
    if not date_text_str or not date_text_str.strip() or date_text_str.lower() == 'n/a':
        return "N/A"
    cleaned_date_text = date_text_str.strip()
    labels = [
        "submission deadline", "offers due", "date offers due", "closing date", 
        "response date", "expiration date", "proposal due date", 
        "application due date", "deadline", "proposers’ day", "proposers day", "proposer's day"
    ]
    for label in labels:
        pattern = rf"^\s*{re.escape(label)}\s*[:\-]?\s*"
        if re.match(pattern, cleaned_date_text, re.IGNORECASE):
            cleaned_date_text = re.sub(pattern, "", cleaned_date_text, count=1, flags=re.IGNORECASE).strip()
            break

    date_patterns = [
        ("%B %d, %Y", r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},\s+\d{4}"),
        ("%m/%d/%Y", r"\d{1,2}/\d{1,2}/\d{4}"),
        ("%Y-%m-%d", r"\d{4}-\d{1,2}-\d{1,2}")
    ]
    for fmt, pattern in date_patterns:
        match = re.search(pattern, cleaned_date_text)
        if match:
            try:
                return datetime.strptime(match.group(0), fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
    return "N/A"

def fetch_arpah_opportunities():
    url = "https://arpa-h.gov/explore-funding/open-funding-opportunities/"
    headers = {
        "User-Agent": "Mozilla/5.0"
    }
    results = []
    try:
        res = requests.get(url, headers=headers, timeout=30)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")

        link_elements = soup.select("div.field--name-body p > a[href], div.field--name-body div.fusion-button-wrapper > a[href]")
        links_to_visit = []
        for el in link_elements:
            href = el.get("href")
            if href and str(href).startswith("http"):
                links_to_visit.append({"url": href, "text_hint": el.get_text(strip=True)})

        logging.info(f"Found {len(links_to_visit)} links.")

        for link in links_to_visit[:25]:
            try:
                detail_res = requests.get(link["url"], headers=headers, timeout=30)
                detail_res.raise_for_status()
                detail_soup = BeautifulSoup(detail_res.text, "html.parser")

                title = link["text_hint"] or "N/A"
                title_el = detail_soup.select_one("h1.page-title, h1.title, h1.wp-block-post-title, h1")
                if title_el:
                    title = title_el.get_text(strip=True)

                desc_el = detail_soup.select_one("article .field--name-body, div.entry-content, main#main-content")
                description = desc_el.get_text(separator=" ", strip=True) if desc_el else "Description not found."

                close_date = "N/A"
                key_dates_section = detail_soup.find(lambda tag: tag.name in ['h4', 'h3'] and 'key dates' in tag.get_text(strip=True).lower())
                if key_dates_section:
                    parent_div = key_dates_section.find_parent("div")
                    if parent_div:
                        lines = parent_div.get_text(separator="\n").splitlines()
                        for line in lines:
                            if any(lbl in line.lower() for lbl in ["closing date", "submission deadline", "proposals due", "deadline", "responses due"]):
                                close_date = parse_date_from_text(line)
                                break

                if close_date != "N/A":
                    try:
                        if datetime.strptime(close_date, "%Y-%m-%d") < datetime.now():
                            continue
                    except ValueError:
                        pass

                results.append({
                    "Source": "ARPA-H",
                    "Title": title,
                    "Description": description,
                    "URL": link["url"],
                    "Close Date": close_date,
                    "ScrapedDate": datetime.now().isoformat()
                })
            except Exception as detail_err:
                logging.warning(f"Error processing {link['url']}: {detail_err}")

    except Exception as e:
        logging.error(f"Error fetching ARPA-H opportunities: {e}")

    return results

if __name__ == "__main__":
    data = fetch_arpah_opportunities()
    print(json.dumps(data, indent=2))

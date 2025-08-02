import httpx
from bs4 import BeautifulSoup, Tag
import logging
from datetime import datetime, timedelta
import time
import json
from urllib.parse import urljoin
from typing import Optional, Dict, List, Any
import asyncio

# --- Module-level logger setup ---
logger = logging.getLogger(__name__)

# --- In-Memory Cache ---
CACHE = {
    "data": [],
    "timestamp": datetime.min
}
CACHE_DURATION = timedelta(hours=24)

def fetch_iarpa_opportunities() -> List[Dict]:
    """
    Synchronous wrapper for the async fetching function.
    Checks the cache before running the scraper.
    """
    if CACHE["data"] and (datetime.now() - CACHE["timestamp"]) < CACHE_DURATION:
        logger.info("[IARPA] Returning cached data.")
        return CACHE["data"]
    
    return asyncio.run(async_fetch_iarpa_opportunities())

async def async_fetch_iarpa_opportunities() -> List[Dict]:
    """
    Asynchronously fetches IARPA opportunities with retry logic and a longer timeout.
    """
    url = "https://www.iarpa.gov/engage-with-us/open-baas"
    logger.info(f"Fetching IARPA Open BAAs page: {url}")
    opportunities = []
    max_retries = 3

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
                response = await client.get(url)
                response.raise_for_status()
            
            soup = BeautifulSoup(response.text, "html.parser")

            content_area = soup.find(id="dnn_ctr497_View_ScopeWrapper")
            if not isinstance(content_area, Tag):
                logger.warning("IARPA content area not found. The site structure may have changed.")
                return []

            baa_blocks = content_area.find_all("article")
            logger.info(f"Found {len(baa_blocks)} BAA blocks.")

            for block in baa_blocks:
                if not isinstance(block, Tag):
                    continue

                title_element = block.find("h2")
                title = title_element.get_text(strip=True) if isinstance(title_element, Tag) else "No Title Found"

                link_element = title_element.find("a") if isinstance(title_element, Tag) else None
                link_url = "No Link Found"
                if isinstance(link_element, Tag):
                    href = link_element.get("href")
                    if href:
                        link_url = urljoin(url, str(href).strip())

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
                logger.info(f"✅ Scraped IARPA BAA: {title}")
            
            CACHE["data"] = opportunities
            CACHE["timestamp"] = datetime.now()
            return opportunities

        except httpx.RequestError as e:
            logger.error(f"Attempt {attempt + 1} of {max_retries} failed for IARPA page: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  
            else:
                logger.error("All retries failed for IARPA. Returning empty list.")
                return []
        except Exception as e:
            logger.error(f"An unexpected error occurred: {e}", exc_info=True)
            return []

    return opportunities

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    data = fetch_iarpa_opportunities()
    print(f"\n--- Scraped {len(data)} IARPA Opportunities ---")
    for item in data:
        print(json.dumps(item, indent=2))

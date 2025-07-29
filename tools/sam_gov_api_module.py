import time
import requests
import logging
from datetime import datetime, timedelta
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, RetryError
import os
import json

# --- Logger Setup ---
module_logger = logging.getLogger(__name__)
if not module_logger.handlers:
    _handler = logging.StreamHandler()
    _formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(funcName)s - %(message)s')
    _handler.setFormatter(_formatter)
    module_logger.addHandler(_handler)
    module_logger.setLevel(logging.INFO)
    module_logger.propagate = False

# --- Retry Decorator ---
@retry(
    wait=wait_exponential(multiplier=1, min=5, max=60),
    stop=stop_after_attempt(3),
    retry=retry_if_exception_type(requests.exceptions.RequestException)
)
def _make_sam_api_request_with_retries(url, params, headers, logger_instance):
    """Makes an API request to SAM.gov with exponential backoff."""
    try:
        response = requests.get(url, params=params, headers=headers, timeout=45)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger_instance.error(f"RequestException during SAM.gov API call to {url}: {e}")
        raise
    except ValueError as e_json:
        logger_instance.error(f"JSONDecodeError: Failed to parse JSON response from {url}: {e_json}")
        raise

# --- Main Fetch Function ---
def fetch_sam_gov_opportunities() -> list:
    module_logger.info("🔍 Scraping SAM.gov via API (with pagination)...")

    # Pull key from Azure env
    api_key = os.getenv("SAM_GOV_API_KEY")
    if not api_key or api_key.strip() == "":
        module_logger.error("❌ SAM.gov API key not set in environment. Skipping SAM.gov scrape.")
        return []

    api_url = "https://api.sam.gov/prod/opportunities/v1/search"

    API_LIMIT_PER_PAGE = 100
    MAX_TOTAL_RECORDS_TO_FETCH = 500
    DELAY_BETWEEN_PAGES_SECONDS = 3
    DAYS_TO_LOOK_BACK = 14

    all_api_results = []
    posted_from_date = (datetime.utcnow() - timedelta(days=DAYS_TO_LOOK_BACK)).strftime("%Y-%m-%d")

    params = {
        "noticeType": "Combined Synopsis/Solicitation,Solicitation,Presolicitation,Special Notice",
        "sort": "-modifiedDate",
        "limit": API_LIMIT_PER_PAGE,
        "postedFrom": posted_from_date,
        "offset": 0
    }

    headers = {
        "Accept": "application/json",
        "User-Agent": "WBI-Dashboard/1.0 (+https://wbi-dashboard-app)",
        "X-API-KEY": api_key
    }

    module_logger.info(f"Applying date filter: fetching opportunities posted from {posted_from_date}")

    current_offset = 0
    records_fetched_this_session = 0

    while True:
        if records_fetched_this_session >= MAX_TOTAL_RECORDS_TO_FETCH:
            module_logger.info(f"Reached MAX_TOTAL_RECORDS_TO_FETCH ({MAX_TOTAL_RECORDS_TO_FETCH}). Stopping.")
            break

        params["offset"] = current_offset
        module_logger.info(f"Querying SAM.gov API. Limit: {API_LIMIT_PER_PAGE}, Offset: {current_offset}")

        try:
            data = _make_sam_api_request_with_retries(api_url, params, headers, module_logger)

            notices = data.get("opportunitiesData", [])
            total_records_for_query = data.get("totalRecords", 0)

            if not notices:
                module_logger.info(f"No more notices found at offset {current_offset}. Total for query was {total_records_for_query}.")
                break

            module_logger.info(f"Retrieved {len(notices)} notices. Total available for query: {total_records_for_query}.")

            for notice in notices:
                title = str(notice.get("title") or "Title Not Available").strip()
                description = str(notice.get("description") or "").strip()
                link = notice.get("uiLink")

                # Fallback link if uiLink missing
                if not link and notice.get("solicitationNumber"):
                    link = f"https://sam.gov/opp/{notice.get('solicitationNumber')}/view"

                close_date = notice.get("responseDeadLine")
                if close_date:
                    try:
                        close_date = datetime.strptime(close_date, "%Y-%m-%dT%H:%M:%S.%f%z").strftime("%Y-%m-%d")
                    except Exception:
                        close_date = str(close_date)  # fallback as string

                all_api_results.append({
                    "Source": "SAM.gov API",
                    "Title": title,
                    "Description": description[:2000],  # prevent bloat
                    "URL": link or "N/A",
                    "ScrapedDate": datetime.utcnow().isoformat(),
                    "Close Date": close_date or "N/A"
                })

                records_fetched_this_session += 1
                if records_fetched_this_session >= MAX_TOTAL_RECORDS_TO_FETCH:
                    break

            current_offset += len(notices)
            if current_offset >= total_records_for_query:
                module_logger.info("Fetched all available records for the current query criteria.")
                break

            module_logger.info(f"Waiting {DELAY_BETWEEN_PAGES_SECONDS} seconds before fetching next page...")
            time.sleep(DELAY_BETWEEN_PAGES_SECONDS)

        except RetryError as e_retry:
            last_exception = e_retry.last_attempt.exception()
            module_logger.error(f"❌ SAM.gov API request failed after retries: {last_exception}", exc_info=False)
            break
        except Exception as e_sam:
            module_logger.error(f"❌ SAM.gov API scraping failed: {e_sam}", exc_info=True)
            break

    module_logger.info(f"✅ Finished. Scraped {len(all_api_results)} total opportunities.")
    return all_api_results

# --- Optional Debug Entry Point ---
if __name__ == '__main__':
    results = fetch_sam_gov_opportunities()
    print(json.dumps(results, indent=2))

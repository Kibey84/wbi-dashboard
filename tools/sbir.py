import requests
import pandas as pd
import time
import logging
from datetime import datetime
import os
from xlsxwriter.utility import xl_rowcol_to_cell

# --- Configuration ---
START_YEAR = datetime.now().year - 1 
CURRENT_YEAR = datetime.now().year
OUTPUT_FILENAME = "Discovered Companies.xlsx"
LOG_FILENAME = "sbir_tool_log.txt"

def fetch_awards_by_year(year: int) -> list[dict]:
    """
    Fetches all Phase II DoD SBIR award data for a specific year from the SBIR.gov API.
    """
    base_url = "https://api.www.sbir.gov/public/api/awards"
    year_awards = []
    start_index = 0
    rows_per_request = 100
    logging.info(f"--- Fetching awards for year: {year} ---")
    
    while True:
        params = {
            'agency': 'DOD', 
            'year': year, 
            'phase': 'Phase II',
            'program': 'SBIR',
            'start': start_index, 
            'rows': rows_per_request
        }
        try:
            response = requests.get(base_url, params=params)
            response.raise_for_status()
            data = response.json()
            
            if not data:
                logging.info(f"No more awards found for {year}.")
                break
            
            year_awards.extend(data)
            logging.info(f"Fetched {len(data)} awards. Total retrieved for year {year}: {len(year_awards)}")
            start_index += rows_per_request
            time.sleep(0.5)

        except requests.exceptions.RequestException as e:
            logging.error(f"An API error occurred for year {year}: {e}")
            break
            
    return year_awards

def fetch_all_awards() -> list[dict]:
    """
    Fetches SBIR awards for all specified years and combines them.
    """
    all_awards = []
    years_to_fetch = range(START_YEAR, CURRENT_YEAR + 1)

    logging.info(f"--- Fetching Phase II DOD SBIR awards for years {START_YEAR}-{CURRENT_YEAR} ---")
    
    for year in years_to_fetch:
        awards_for_year = fetch_awards_by_year(year)
        all_awards.extend(awards_for_year)
            
    return all_awards

def process_and_save_data(df: pd.DataFrame, filename: str) -> None:
    """
    Processes the DataFrame and saves it as a formatted Excel file.
    """
    logging.info(f"Saving processed data to {filename}...")
    try:
        excel_df = df.copy()

        with pd.ExcelWriter(filename, engine='xlsxwriter') as writer:
            excel_df.to_excel(writer, sheet_name='Discovered SBIRs', index=False)
            
            workbook  = writer.book
            worksheet = writer.sheets['Discovered SBIRs']
            currency_format = workbook.add_format({'num_format': '$#,##0'}) # type: ignore

            title_col_idx = excel_df.columns.get_loc('Award Title')
            url_col_idx = excel_df.columns.get_loc('award_link')
            amount_col_idx = excel_df.columns.get_loc('award_amount')

            for row_num, row in enumerate(excel_df.itertuples(), 1):
                link_url = str(getattr(row, 'award_link', ''))
                title_string = str(getattr(row, 'Award Title', ''))

                if link_url.startswith(('http://', 'https://')):
                    worksheet.write_url(row_num, title_col_idx, link_url, string=title_string)
                else:
                    worksheet.write(row_num, title_col_idx, title_string)

            for idx, col in enumerate(excel_df.columns):
                series = excel_df[col]
                max_len = max((
                    series.astype(str).map(len).max(),
                    len(str(series.name))
                )) + 2
                
                if idx == amount_col_idx:
                    worksheet.set_column(idx, idx, max_len, currency_format)
                else:
                    worksheet.set_column(idx, idx, max_len)

        logging.info(f"Successfully saved data to {filename}")
    except Exception as e:
        logging.error(f"Failed to save Excel file: {e}", exc_info=True)

def run_phase_1(testing_mode=False):
    """
    Main function to run the scraping and processing for Phase 1.
    """
    logging.info("--- SBIR Award Scraper and Processor (Phase 1) Started ---")
    
    all_awards = fetch_all_awards() 
    if not all_awards:
        logging.warning("No awards found to process.")
        return

    df = pd.DataFrame(all_awards)
    
    if 'program' in df.columns:
        initial_count = len(df)
        df = df[df['program'] == 'SBIR'].copy()
        logging.info(f"Filtered down to {len(df)} SBIR-only awards from {initial_count} total.")

    process_and_save_data(df, OUTPUT_FILENAME)

    logging.info("--- Phase 1 Script Finished ---")

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    run_phase_1()

import requests
import pandas as pd
import time
import logging
from datetime import datetime

# --- Configuration ---
START_YEAR = datetime.now().year - 1 
CURRENT_YEAR = datetime.now().year
OUTPUT_FILENAME = "Discovered Companies.xlsx"
LOG_FILENAME = "sbir_tool_log.txt"

def fetch_awards_by_year(start_year: int) -> list[dict]:
    """
    Fetches all Phase II DoD SBIR award data from the SBIR.gov API.
    Now starts from the specified start_year instead of a hardcoded one.
    """
    base_url = "https://api.www.sbir.gov/public/api/awards"
    all_awards = []
    
    logging.info(f"--- Fetching Phase II DOD SBIR awards for years {start_year}-{CURRENT_YEAR} ---")

    for year in range(start_year, CURRENT_YEAR + 1):
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
                
                all_awards.extend(data)
                logging.info(f"Fetched {len(data)} awards. Total retrieved so far: {len(all_awards)}")
                start_index += rows_per_request
                time.sleep(0.5)

            except requests.exceptions.RequestException as e:
                logging.error(f"An API error occurred for year {year}: {e}")
                break
            
    return all_awards

def process_and_save_data(data: list[dict], filename: str) -> None:
    """
    Processes the raw award data and saves it to a formatted Excel file.
    (This function remains the same as the last corrected version)
    """
    if not data:
        logging.warning("No data was provided to process and save.")
        return
        
    logging.info(f"\nProcessing {len(data)} total awards...")
    df = pd.DataFrame(data)
    df = df[df['program'] == 'SBIR'].copy()
    logging.info(f"Filtered down to {len(df)} SBIR-only awards.")

    columns_to_drop = [
        'agency', 'agency_tracking_number', 'solicitation_number', 'solicitation_year',
        'topic_code', 'award_year', 'duns', 'uei', 'abstract', 'pi_name',
        'pi_email', 'pi_phone', 'research_institution', 'ri_duns', 'ri_uei',
        'hubzone_owned', 'socially_economically_disadvantaged', 'woman_owned', 'firm_woman_owned',
        'poc_name', 'poc_email', 'poc_phone', 'poc_title', 'firm_hubzone_owned',
        'firm_socially_economically_disadvantaged', 'firm_number_awards',
        'number_awards', 'employee_count', 'number_employees', 'veteran_owned',
        'pi_title', 'ri_name', 'ri_poc_name', 'ri_poc_phone'
    ]
    df.drop(columns=columns_to_drop, inplace=True, errors='ignore')

    if 'proposal_award_date' in df.columns:
        df['proposal_award_date'] = pd.to_datetime(df['proposal_award_date'], errors='coerce').dt.date

    df['Company_Award_Count'] = df.groupby('firm')['firm'].transform('size')
    df = df.sort_values(by=['Company_Award_Count', 'proposal_award_date'], ascending=[False, False])
    df.rename(columns={'award_title': 'Award Title', 'link': 'award_link'}, inplace=True)

    logging.info(f"Saving processed data to {filename}...")
    try:
        excel_df = df.drop(columns=['award_link'], errors='ignore')
        with pd.ExcelWriter(filename, engine='xlsxwriter') as writer:
            excel_df.to_excel(writer, sheet_name='Discovered SBIRs', index=False)
            workbook  = writer.book
            worksheet = writer.sheets['Discovered SBIRs']
            currency_format = workbook.add_format({'num_format': '$#,##0.00'}) # type: ignore
            title_col_idx = excel_df.columns.get_loc('Award Title')
            amount_col_idx = excel_df.columns.get_loc('award_amount')
            for row_num, link_url in enumerate(df['award_link'], 1):
                title_string = df.iloc[row_num - 1]['Award Title']
                if pd.notna(link_url):
                    if not isinstance(link_url, str):
                        link_url = str(link_url)
                    worksheet.write_url(row_num, title_col_idx, link_url, string=str(title_string))
            worksheet.set_column(amount_col_idx, amount_col_idx, 15, currency_format)
            for idx, col in enumerate(excel_df.columns):
                if idx == amount_col_idx: continue
                series = excel_df[col]
                max_len = max((series.astype(str).map(len).max(), len(str(series.name)))) + 2
                worksheet.set_column(idx, idx, max_len)
        logging.info(f"Successfully saved data to {filename}")
    except Exception as e:
        logging.error(f"Failed to save Excel file: {e}", exc_info=True)

def run_phase_1(testing_mode=False):
    """ Main execution function for Phase 1. """
    logging.info("--- SBIR Award Scraper and Processor (Phase 1) Started ---")
    
    # If testing, just use the current year to go even faster
    start_year_to_fetch = CURRENT_YEAR if testing_mode else START_YEAR
    
    awards_data = fetch_awards_by_year(start_year_to_fetch)
    if awards_data:
        process_and_save_data(awards_data, OUTPUT_FILENAME)
    else:
        logging.warning("Phase 1 finished, but no awards were fetched.")
    logging.info("--- Phase 1 Script Finished ---")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[logging.FileHandler(LOG_FILENAME, mode='w'), logging.StreamHandler()], force=True)
    run_phase_1()

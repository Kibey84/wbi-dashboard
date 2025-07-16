import pandas as pd
import time
import logging
import requests
from datetime import datetime
import json
import random
import os
from docx import Document
from docx.shared import Pt, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
import glob
import re
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

# --- Configuration ---
API_KEY = "AIzaSyATWks4abnLcnPfp7UF_nwTIxn326viBBo" 
DOSSIER_FOLDER = "company_dossiers" 
OUTPUT_FILENAME = "sbir_top_30_ranked.docx"
LOG_FILENAME = "sbir_grading_log.txt"
MAX_RANKING_LIMIT = 30
# The input file from Phase 1, which contains the company URLs
COMPANY_DATA_SOURCE_FILE = "Discovered Companies.xlsx"

# NOTE: The logging basicConfig is moved to the if __name__ == "__main__": block

# --- Define Grading Weights ---
GRADING_WEIGHTS = {
    'Technology_Strength': 0.30,
    'Market_Traction': 0.30,
    'Team_Experience': 0.20,
    'DoD_Alignment': 0.20
}

def get_ai_grading(dossier_text):
    """Instructs the Gemini API to grade a company based on its dossier."""
    if not API_KEY or "YOUR_API_KEY" in API_KEY:
        logging.error("API Key is missing. Please configure it before running.")
        return {"error": "Grading requires a valid API Key."}

    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={API_KEY}"
    prompt = f"""
    Act as a lead analyst on a venture capital investment committee...
    Return your response *only* as a valid JSON object...
    {{
      "Technology_Strength": {{ "score": <number>, "justification": "<text>" }},
      "Market_Traction": {{ "score": <number>, "justification": "<text>" }},
      "Team_Experience": {{ "score": <number>, "justification": "<text>" }},
      "DoD_Alignment": {{ "score": <number>, "justification": "<text>" }}
    }}
    Dossier to Analyze:
    ---
    {dossier_text}
    """ # Prompt shortened for brevity
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    max_retries = 3
    base_delay = 5

    for attempt in range(max_retries):
        try:
            response = requests.post(api_url, json=payload, headers={'Content-Type': 'application/json'}, timeout=120)
            if response.status_code == 200:
                result = response.json()
                try:
                    raw_text = result['candidates'][0]['content']['parts'][0]['text']
                except (KeyError, IndexError, TypeError) as e:
                    return {"error": f"Invalid response structure from AI: {e}"}
                
                match = re.search(r'\{.*\}', raw_text, re.DOTALL)
                json_text = match.group(0) if match else '{}'
                return json.loads(json_text)

            elif response.status_code in [429, 503]:
                time.sleep(base_delay * (2 ** attempt) + random.uniform(0, 1))
            else:
                response.raise_for_status()
        except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
            if attempt >= max_retries - 1:
                return {"error": f"Failed after multiple retries: {e}"}
            time.sleep(base_delay * (2 ** attempt))
    return {"error": "Grading failed after multiple retries."}

def load_company_urls(source_file):
    """Loads company names and URLs from the Phase 1 Excel file."""
    try:
        df = pd.read_excel(source_file)
        # Use a list of possible column names for the URL
        possible_url_cols = ['company_url', 'firm_company_url']
        url_col_name = next((col for col in possible_url_cols if col in df.columns), None)

        if 'firm' in df.columns and url_col_name:
            # Fill missing URLs with a placeholder string to avoid float NaNs
            df[url_col_name] = df[url_col_name].fillna("N/A")
            company_url_map = df.drop_duplicates(subset='firm').set_index('firm')[url_col_name].to_dict()
            logging.info(f"Successfully loaded {len(company_url_map)} company URLs from {source_file}.")
            return company_url_map
        else:
            logging.error(f"Source file '{source_file}' is missing 'firm' or a valid URL column.")
            return {}
    except FileNotFoundError:
        logging.error(f"Company data source file not found: '{source_file}'. URLs will not be added.")
        return {}
    except Exception as e:
        logging.error(f"Error loading company URLs: {e}")
        return {}

def run_grading_process(dossier_folder, company_urls):
    """Reads all dossiers, sends them for AI grading, and compiles the results."""
    logging.info(f"Starting grading process. Reading dossiers from '{dossier_folder}'")
    
    if not os.path.isdir(dossier_folder):
        logging.error(f"FATAL: The specified dossier folder does not exist: '{dossier_folder}'")
        return None

    dossier_paths = glob.glob(os.path.join(dossier_folder, "*.docx"))
    
    if not dossier_paths:
        logging.error(f"FATAL: No .docx files found in the '{dossier_folder}' directory.")
        return None
        
    logging.info(f"Found {len(dossier_paths)} company dossiers to grade.")
    
    all_graded_data = []

    for path in dossier_paths:
        company_name = "Unknown"
        try:
            doc = Document(path)
            if not doc.paragraphs: continue

            company_name = doc.paragraphs[0].text.replace("Dossier:", "").strip()
            logging.info(f"--- Grading: {company_name} ---")

            full_text = "\n".join([p.text for p in doc.paragraphs])
            grading_data = get_ai_grading(full_text)
            
            if not isinstance(grading_data, dict) or "error" in grading_data:
                error_message = grading_data.get("error", "Unknown error") if isinstance(grading_data, dict) else str(grading_data)
                logging.error(f"Could not grade {company_name}: {error_message}")
                continue

            tech_score = grading_data.get('Technology_Strength', {}).get('score', 0) #type: ignore
            market_score = grading_data.get('Market_Traction', {}).get('score', 0) #type: ignore
            team_score = grading_data.get('Team_Experience', {}).get('score', 0) #type: ignore
            dod_score = grading_data.get('DoD_Alignment', {}).get('score', 0) #type: ignore

            smart_bet_score = (
                (tech_score or 0) * GRADING_WEIGHTS['Technology_Strength'] +
                (market_score or 0) * GRADING_WEIGHTS['Market_Traction'] +
                (team_score or 0) * GRADING_WEIGHTS['Team_Experience'] +
                (dod_score or 0) * GRADING_WEIGHTS['DoD_Alignment']
            )
            
            report_row = {
                'Company': company_name,
                'Company_URL': company_urls.get(company_name, "N/A"),
                'Smart_Bet_Score': round(smart_bet_score, 2),
                'Tech_Strength_Score': tech_score,
                'Tech_Strength_Justification': grading_data.get('Technology_Strength', {}).get('justification'), #type: ignore
                'Market_Traction_Score': market_score,
                'Market_Traction_Justification': grading_data.get('Market_Traction', {}).get('justification'), #type: ignore
                'Team_Experience_Score': team_score,
                'Team_Experience_Justification': grading_data.get('Team_Experience', {}).get('justification'), #type: ignore
                'DoD_Alignment_Score': dod_score,
                'DoD_Alignment_Justification': grading_data.get('DoD_Alignment', {}).get('justification'), #type: ignore
            }
            all_graded_data.append(report_row)
            logging.info(f"Finished grading {company_name}. Score: {smart_bet_score:.2f}")
            time.sleep(2) 

        except Exception as e:
            logging.error(f"A critical error occurred processing dossier for '{company_name}': {e}", exc_info=True)
            continue
            
    return pd.DataFrame(all_graded_data)

def add_hyperlink(paragraph, url, text):
    """Adds a hyperlink with a specific style to a paragraph."""
    part = paragraph.part
    r_id = part.relate_to(url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)

    hyperlink = OxmlElement('w:hyperlink')
    hyperlink.set(qn('r:id'), r_id)

    new_run = OxmlElement('w:r')
    rPr = OxmlElement('w:rPr')
    
    c = OxmlElement('w:color')
    c.set(qn('w:val'), "0563C1")
    rPr.append(c)
    
    u = OxmlElement('w:u')
    u.set(qn('w:val'), 'single')
    rPr.append(u)
    
    new_run.append(rPr)
    t = OxmlElement('w:t')
    t.text = text # type: ignore
    new_run.append(t)
    hyperlink.append(new_run)
    paragraph._p.append(hyperlink)

def save_graded_report(df, filename):
    """Saves the final graded report to a formatted Word document."""
    if df is None or df.empty:
        logging.warning("No graded data to save.")
        return

    logging.info(f"Saving top {MAX_RANKING_LIMIT} ranked companies to {filename}...")
    
    try:
        df_sorted = df.sort_values(by='Smart_Bet_Score', ascending=False)
        df_top = df_sorted.head(MAX_RANKING_LIMIT)

        doc = Document()
        doc.add_heading('SBIR Company Grading & Ranking Report', level=0)
        p_date = doc.add_paragraph(f"Report generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        p_date.alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        doc.add_page_break()

        for index, row in df_top.iterrows():
            doc.add_heading(f"Rank #{df_top.index.get_loc(index) + 1}: {row['Company']}", level=1)
            
            # FIX: Ensure company_url is a string and is a valid URL before creating a hyperlink
            company_url = row.get('Company_URL')
            if isinstance(company_url, str) and company_url.startswith('http'):
                p_url = doc.add_paragraph()
                add_hyperlink(p_url, company_url, company_url)
                p_url.alignment = WD_ALIGN_PARAGRAPH.LEFT

            p_score = doc.add_paragraph()
            p_score.add_run('Overall Smart Bet Score: ').bold = True
            p_score.add_run(str(row['Smart_Bet_Score'])).font.size = Pt(14)
            doc.add_heading('Detailed Assessment', level=2)
            
            table = doc.add_table(rows=1, cols=3)
            table.style = 'Light Shading Accent 1'
            hdr_cells = table.rows[0].cells
            hdr_cells[0].text, hdr_cells[1].text, hdr_cells[2].text = 'Category', 'Score', 'Justification'
            
            categories = ['Technology_Strength', 'Market_Traction', 'Team_Experience', 'DoD_Alignment']
            for cat in categories:
                row_cells = table.add_row().cells
                row_cells[0].text = cat.replace('_', ' ')
                row_cells[1].text = str(row.get(f'{cat}_Score', 'N/A'))
                row_cells[2].text = str(row.get(f'{cat}_Justification', 'N/A'))

            doc.add_paragraph() 

        doc.save(filename)
        logging.info(f"Successfully saved Word report to {filename}")

    except Exception as e:
        logging.error(f"Failed to save final Word report: {e}", exc_info=True)

def run_phase_3():
    """Main execution function for Phase 3."""
    logging.info("--- Phase 3: AI Grading & Ranking Module Started ---")
    
    company_url_map = load_company_urls(COMPANY_DATA_SOURCE_FILE)
    graded_df = run_grading_process(DOSSIER_FOLDER, company_url_map)
    
    if graded_df is not None:
        save_graded_report(graded_df, OUTPUT_FILENAME)
        
    logging.info("--- Phase 3 Script Finished ---")

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[logging.FileHandler(LOG_FILENAME, mode='w'), logging.StreamHandler()],
        force=True
    )
    run_phase_3()

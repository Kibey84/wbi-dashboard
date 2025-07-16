import pandas as pd
import time
import logging
import requests
from datetime import datetime
import json
import random
import os
from docx import Document
from docx.shared import Inches, Pt
import re

# --- Configuration ---
API_KEY = "AIzaSyATWks4abnLcnPfp7UF_nwTIxn326viBBo"
# This input filename must match the output filename from sbir.py
INPUT_FILENAME = "Discovered Companies.xlsx" 
OUTPUT_FOLDER = "company_dossiers"
LOG_FILENAME = "sbir_research_log.txt"

# NOTE: The logging basicConfig is moved to the `if __name__ == "__main__":`
# block. This allows a master run.py script to control the logging for the pipeline.

def clean_ai_response(text):
    """Removes conversational filler from the beginning of the AI's response."""
    patterns_to_remove = [
        r"^\s*Okay,.*?dossier\s*\n",
        r"^\s*Here is the dossier:?\s*\n",
        r"^\s*Okay, initiating.*?Complete\.\.\.\s*\n",
        r"^\s*Okay, here's.*?dossier\s*\n",
        r"^\s*Okay, here is my analysis of.*?as requested\.\s*\n",
        r"^\s*Okay, here's the venture capital analyst dossier.*?\n"
    ]
    cleaned_text = text
    for pattern in patterns_to_remove:
        cleaned_text = re.sub(pattern, '', cleaned_text, flags=re.IGNORECASE | re.DOTALL)
    return cleaned_text.strip()

def get_ai_research_summary(company_name):
    """
    Instructs the Gemini API to perform deep web research and generate a detailed summary.
    """
    if not API_KEY or "YOUR_API_KEY_HERE" in API_KEY:
        return "AI summary requires a valid API Key."

    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={API_KEY}"
    
    prompt = f"""
    Act as a senior venture capital analyst specializing in the defense and aerospace sectors. Your task is to conduct a deep, comprehensive web search to gather actionable intelligence on the US-based company: '{company_name}'.

    Synthesize your findings into a professional dossier using the following strict format. Do not include any introductory conversational text.

    **Company Overview:**
    In one detailed paragraph, what is this company's primary business, its core mission, and the key problems it aims to solve? What is their unique value proposition?

    **Technology Focus:**
    In 2-3 bullet points, describe the company's specific technology, products, or services. Be specific and quantitative where possible.

    **Recent Developments & Traction:**
    In 2-4 bullet points, list significant recent news, milestones, major partnerships (especially with government/DoD), funding rounds (include date, amount, and lead investors if possible), or product launches from the last 2-3 years.

    **Leadership & Team:**
    List the key leaders (CEO, CTO, President) and briefly note any highly relevant prior experience (e.g., previous successful startups, senior military roles, major tech company experience).

    **Competitive Landscape:**
    Identify 1-2 primary competitors and briefly explain this company's key differentiator.

    **Sources:**
    List the top 3-5 most informative URLs (excluding social media homepages) that you used for this analysis.
    """
    
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    max_retries = 3
    base_delay = 5 

    for attempt in range(max_retries):
        try:
            response = requests.post(api_url, json=payload, headers={'Content-Type': 'application/json'}, timeout=150)
            
            if response.status_code == 200:
                result = response.json()
                raw_text = result.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', "Could not generate AI summary from response.")
                return clean_ai_response(raw_text)
            
            elif response.status_code in [429, 503]:
                delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                logging.warning(f"API Error ({response.status_code}) for {company_name}. Rate-limited. Cooling down for {delay:.2f}s.")
                time.sleep(delay)
            else:
                response.raise_for_status()

        except requests.exceptions.RequestException as e:
            logging.error(f"Request failed for {company_name}: {e}")
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logging.warning(f"Retrying after {delay}s...")
                time.sleep(delay)
            else:
                logging.error(f"All retries failed for {company_name}.")
                return "AI summary failed due to repeated API errors."

    return "AI summary failed after all retries."

def create_company_dossier(company_name, award_data, ai_summary):
    """Creates a formatted Word document for a single company."""
    doc = Document()
    doc.add_heading(f'Dossier: {company_name}', level=1)
    
    doc.add_heading('SBIR Award Details', level=2)
    p = doc.add_paragraph()
    p.add_run('Award Title: ').bold = True
    p.add_run(str(award_data.get('award_title', 'N/A')))
    p = doc.add_paragraph()
    p.add_run('Amount: ').bold = True
    p.add_run(f"${float(award_data.get('award_amount', 0)):,.2f}")
    p = doc.add_paragraph()
    p.add_run('Award Date: ').bold = True
    # Ensure date is formatted correctly, even if it's already a datetime object
    award_date = award_data.get('proposal_award_date', 'N/A')
    if isinstance(award_date, datetime):
        p.add_run(award_date.strftime('%Y-%m-%d'))
    else:
        p.add_run(str(award_date))

    p = doc.add_paragraph()
    p.add_run('Branch: ').bold = True
    p.add_run(str(award_data.get('branch', 'N/A')))

    doc.add_heading('AI-Generated Intelligence Summary', level=2)
    
    for line in ai_summary.split('\n'):
        line = line.strip()
        if line.startswith('**'):
            clean_line = line.replace('**', '').strip()
            p = doc.add_paragraph()
            p.add_run(clean_line).bold = True
        elif line.startswith('*'):
            doc.add_paragraph(line.lstrip('* ').strip(), style='List Bullet')
        elif line: 
            doc.add_paragraph(line)

    if not os.path.exists(OUTPUT_FOLDER):
        os.makedirs(OUTPUT_FOLDER)
        
    safe_filename = "".join([c for c in company_name if c.isalpha() or c.isdigit() or c==' ']).rstrip()
    filepath = os.path.join(OUTPUT_FOLDER, f"{safe_filename}.docx")
    
    try:
        doc.save(filepath)
        logging.info(f"Successfully saved dossier to {filepath}")
    except Exception as e:
        logging.error(f"Could not save document for {company_name}: {e}")

def run_research_and_generate_dossiers(input_file):
    """
    Main function to read companies, get AI research, and generate Word documents.
    """
    logging.info(f"Starting AI research. Reading data from {input_file}")
    try:
        df = pd.read_excel(input_file)
    except FileNotFoundError:
        logging.error(f"FATAL: Input file not found at '{input_file}'. Ensure Phase 1 ran successfully.")
        return

    unique_companies = df.drop_duplicates(subset='firm')
    logging.info(f"Found {len(unique_companies)} unique companies to research and create dossiers for.")

    if not unique_companies.empty:
        logging.info("Performing a one-time 15-second startup delay before first API call.")
        time.sleep(15)

    for index, company_row in unique_companies.iterrows():
        company_name = company_row['firm']
        logging.info(f"--- Processing Dossier for: {company_name} ---")
        
        try:
            summary = get_ai_research_summary(company_name)
            logging.info(f"AI Summary for {company_name} generated.")
            
            create_company_dossier(company_name, company_row, summary)
            
            # Short, polite pause between calls.
            time.sleep(2) 

        except Exception as e:
            logging.error(f"A critical error occurred processing {company_name}: {e}")
            continue

def run_phase_2():
    """
    Main execution function for Phase 2.
    """
    logging.info("--- Phase 2: AI Research Dossier Generator Started ---")
    run_research_and_generate_dossiers(INPUT_FILENAME)
    logging.info("--- Phase 2 Script Finished ---")


if __name__ == "__main__":
    # This block only runs when the script is executed directly.
    # It allows the script to have its own logging when run standalone.
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(LOG_FILENAME, mode='w'),
            logging.StreamHandler()
        ],
        force=True
    )
    run_phase_2()

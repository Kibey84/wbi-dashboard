import os
import pandas as pd
import time
import logging
import asyncio
import json
import re
from datetime import datetime
from typing import List, Dict, Optional

# Document and file handling imports
from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
import glob
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from openai import AsyncAzureOpenAI

# ====== CONFIGURATION & CONSTANTS ======
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT")

DOSSIER_FOLDER = "company_dossiers"
OUTPUT_FILENAME = "sbir_top_30_ranked.docx"
COMPANY_DATA_SOURCE_FILE = "Discovered Companies.xlsx"
LOG_FILENAME = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sbir_grading_log.txt")

MAX_RANKING_LIMIT = 30
GRADING_WEIGHTS = {
    'Technology_Strength': 0.30,
    'Market_Traction': 0.30,
    'Team_Experience': 0.20,
    'DoD_Alignment': 0.20
}

# ====== AI GRADING FUNCTION ======
async def get_ai_grading(client: AsyncAzureOpenAI, dossier_text: str) -> Dict:
    """Asynchronously grades a company dossier and robustly parses the JSON response."""
    
    assert AZURE_OPENAI_DEPLOYMENT is not None

    system_prompt = "You are a lead analyst on a venture capital investment committee, specializing in DoD technology."
    user_prompt = f"""
    Analyze the following company dossier and provide a quantitative and qualitative assessment.
    Return your response ONLY as a valid JSON object with the following structure:
    {{
        "Technology_Strength": {{"score": <float 0.0-1.0>, "justification": "<text>"}},
        "Market_Traction": {{"score": <float 0.0-1.0>, "justification": "<text>"}},
        "Team_Experience": {{"score": <float 0.0-1.0>, "justification": "<text>"}},
        "DoD_Alignment": {{"score": <float 0.0-1.0>, "justification": "<text>"}}
    }}

    Dossier Text:
    ---
    {dossier_text}
    """
    
    try:
        response = await client.chat.completions.create(
            model=AZURE_OPENAI_DEPLOYMENT,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1,
            max_tokens=2000
        )
        
        raw_content = response.choices[0].message.content
        if not raw_content:
            raise ValueError("AI returned an empty response.")

        match = re.search(r'\{.*\}', raw_content, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError as e:
                logging.error(f"Failed to decode extracted JSON: {e}. Content: {match.group(0)}")
                return {"error": "AI returned a malformed JSON object."}
        else:
            logging.error(f"No valid JSON object found in AI response: {raw_content}")
            return {"error": "AI returned a non-JSON response."}
            
    except Exception as e:
        logging.error(f"AI grading failed: {e}", exc_info=True)
        return {"error": str(e)}

# ====== HELPER FUNCTIONS ======
def load_company_urls(source_file: str) -> Dict[str, str]:
    try:
        if not os.path.exists(source_file):
            logging.warning(f"Company data source file not found at: {source_file}")
            return {}
        df = pd.read_excel(source_file)
        url_col = next((col for col in ['company_url', 'firm_company_url'] if col in df.columns), None)
        if 'firm' in df.columns and url_col:
            df[url_col] = df[url_col].fillna("N/A")
            return df.drop_duplicates(subset='firm').set_index('firm')[url_col].to_dict()
        return {}
    except Exception as e:
        logging.error(f"Error loading company URLs: {e}")
        return {}

def add_hyperlink(paragraph, url: str, text: str):
    part = paragraph.part
    r_id = part.relate_to(url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
    hyperlink = OxmlElement('w:hyperlink')
    hyperlink.set(qn('r:id'), r_id)
    new_run = OxmlElement('w:r')
    rPr = OxmlElement('w:rPr')
    c = OxmlElement('w:color'); c.set(qn('w:val'), "0563C1"); rPr.append(c)
    u = OxmlElement('w:u'); u.set(qn('w:val'), 'single'); rPr.append(u)
    new_run.append(rPr)
    t = OxmlElement('w:t'); t.text = text; new_run.append(t)
    hyperlink.append(new_run)
    paragraph._p.append(hyperlink)
    return hyperlink

def save_graded_report(df: pd.DataFrame, filename: str):
    if df.empty:
        logging.warning("No graded data to save.")
        return
    df_sorted = df.sort_values(by='Smart_Bet_Score', ascending=False).head(MAX_RANKING_LIMIT)
    doc = Document()
    doc.add_heading('SBIR Company Grading & Ranking Report', level=0)
    p_date = doc.add_paragraph(f"Report generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    p_date.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_page_break()
    
    for rank, (idx, row) in enumerate(df_sorted.iterrows(), 1):
        doc.add_heading(f"Rank #{rank}: {row['Company']}", level=1)
        url = row.get('Company_URL', '')
        if isinstance(url, str) and url.startswith('http'):
            p_url = doc.add_paragraph()
            add_hyperlink(p_url, url, url)
        p_score = doc.add_paragraph()
        p_score.add_run('Overall Smart Bet Score: ').bold = True
        p_score.add_run(f"{row['Smart_Bet_Score']:.2f}").font.size = Pt(14)
        doc.add_heading('Detailed Assessment', level=2)
        table = doc.add_table(rows=1, cols=3)
        hdr_cells = table.rows[0].cells
        hdr_cells[0].text, hdr_cells[1].text, hdr_cells[2].text = 'Category', 'Score', 'Justification'
        for cat in ['Technology_Strength', 'Market_Traction', 'Team_Experience', 'DoD_Alignment']:
            row_cells = table.add_row().cells
            row_cells[0].text = cat.replace('_', ' ')
            row_cells[1].text = str(row.get(f'{cat}_Score', 'N/A'))
            row_cells[2].text = str(row.get(f'{cat}_Justification', 'N/A'))
        doc.add_paragraph()
    doc.save(filename)
    logging.info(f"Saved graded report to {filename}")

def safe_get_score(score_dict: Optional[Dict]) -> float:
    if isinstance(score_dict, dict) and 'score' in score_dict:
        return float(score_dict['score'])
    return 0.0

def safe_get_justification(score_dict: Optional[Dict]) -> str:
    if isinstance(score_dict, dict) and 'justification' in score_dict:
        return score_dict['justification']
    return 'N/A'

# ====== MAIN ORCHESTRATOR ======
async def run_grading_process():
    if not os.path.isdir(DOSSIER_FOLDER):
        logging.error(f"Dossier folder does not exist: {DOSSIER_FOLDER}")
        return
        
    if not all([AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY, AZURE_OPENAI_DEPLOYMENT]):
        logging.error("Azure OpenAI credentials not fully set. Halting grading process.")
        return

    company_urls = load_company_urls(COMPANY_DATA_SOURCE_FILE)
    all_graded_results = []
    
    dossier_files = glob.glob(os.path.join(DOSSIER_FOLDER, "*.docx"))
    logging.info(f"Found {len(dossier_files)} dossiers to process.")

    assert AZURE_OPENAI_ENDPOINT is not None
    assert AZURE_OPENAI_KEY is not None
    client = AsyncAzureOpenAI(
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_key=AZURE_OPENAI_KEY,
        api_version="2024-02-01"
    )

    tasks = []
    company_data_map = {}
    for file_path in dossier_files:
        try:
            doc = Document(file_path)
            if not doc.paragraphs: continue
            
            company_name = doc.paragraphs[0].text.replace("Dossier:", "").strip()
            full_text = "\n".join(p.text for p in doc.paragraphs)
            
            tasks.append(get_ai_grading(client, full_text))
            company_data_map[company_name] = {'url': company_urls.get(company_name, 'N/A')}
        except Exception as e:
            logging.error(f"Error reading dossier {file_path}: {e}")
    
    ai_grades = await asyncio.gather(*tasks)

    for (company_name, data), grade in zip(company_data_map.items(), ai_grades):
        if not isinstance(grade, dict) or 'error' in grade:
            logging.error(f"Failed grading for {company_name}: {grade.get('error')}")
            continue

        tech_score = safe_get_score(grade.get('Technology_Strength'))
        market_score = safe_get_score(grade.get('Market_Traction'))
        team_score = safe_get_score(grade.get('Team_Experience'))
        dod_score = safe_get_score(grade.get('DoD_Alignment'))

        smart_bet = (
            (tech_score * GRADING_WEIGHTS['Technology_Strength']) +
            (market_score * GRADING_WEIGHTS['Market_Traction']) +
            (team_score * GRADING_WEIGHTS['Team_Experience']) +
            (dod_score * GRADING_WEIGHTS['DoD_Alignment'])
        )
        
        all_graded_results.append({
            'Company': company_name,
            'Company_URL': data['url'],
            'Smart_Bet_Score': round(smart_bet, 2),
            'Technology_Strength_Score': tech_score,
            'Technology_Strength_Justification': safe_get_justification(grade.get('Technology_Strength')),
            'Market_Traction_Score': market_score,
            'Market_Traction_Justification': safe_get_justification(grade.get('Market_Traction')),
            'Team_Experience_Score': team_score,
            'Team_Experience_Justification': safe_get_justification(grade.get('Team_Experience')),
            'DoD_Alignment_Score': dod_score,
            'DoD_Alignment_Justification': safe_get_justification(grade.get('DoD_Alignment'))
        })
        logging.info(f"Graded {company_name} with score {smart_bet:.2f}")

    if all_graded_results:
        df = pd.DataFrame(all_graded_results)
        save_graded_report(df, OUTPUT_FILENAME)

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.FileHandler(LOG_FILENAME, mode='w'), logging.StreamHandler()]
    )
    asyncio.run(run_grading_process())

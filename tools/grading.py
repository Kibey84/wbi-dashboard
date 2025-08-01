import os
import pandas as pd
import time
import logging
import asyncio
import json
from datetime import datetime
from dotenv import load_dotenv
import re  

# Document and file handling imports
from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
import glob
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

from azure.ai.inference.aio import ChatCompletionsClient
from azure.ai.inference.models import SystemMessage, UserMessage
from azure.core.credentials import AzureKeyCredential

# ====== CONFIGURATION & CONSTANTS ======
AZURE_AI_ENDPOINT = os.getenv("AZURE_AI_ENDPOINT")
AZURE_AI_KEY = os.getenv("AZURE_AI_KEY")
AZURE_AI_MODEL_NAME = os.getenv("AZURE_OPENAI_MODEL", "gpt-4")

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
DOSSIER_FOLDER = "company_dossiers"
OUTPUT_FILENAME = "sbir_top_30_ranked.docx"
LOG_FILENAME = os.path.join(TOOLS_DIR, "sbir_grading_log.txt")
COMPANY_DATA_SOURCE_FILE = "Discovered Companies.xlsx"

MAX_RANKING_LIMIT = 30
GRADING_WEIGHTS = {
    'Technology_Strength': 0.30,
    'Market_Traction': 0.30,
    'Team_Experience': 0.20,
    'DoD_Alignment': 0.20
}

# ====== AI GRADING FUNCTION (UPDATED FOR AZURE) ======
async def get_ai_grading(dossier_text):
    """Asynchronously grades a company dossier using the Azure AI model."""
    endpoint = os.getenv("AZURE_AI_ENDPOINT")
    key = os.getenv("AZURE_AI_KEY")
    
    if not all([endpoint, key, AZURE_AI_MODEL_NAME]):
        logging.error("Azure AI credentials not fully set in .env file.")
        return {"error": "Azure AI credentials not set."}

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
    
    async def call_azure_api(valid_endpoint, valid_key):
        try:
            client = ChatCompletionsClient(endpoint=valid_endpoint, credential=AzureKeyCredential(valid_key))
            response = await client.complete(
                deployment_name=AZURE_AI_MODEL_NAME,
                messages=[
                    SystemMessage(content=system_prompt),
                    UserMessage(content=user_prompt)
                ],
                temperature=0.0,
                max_tokens=1500
            )
            ai_message = response.choices[0].message.content
            match = re.search(r'\{.*\}', ai_message, re.DOTALL)
            if match:
                return json.loads(match.group(0))
            else:
                raise ValueError("No valid JSON object found in AI response.")
        except Exception as e:
            logging.error(f"AI grading failed: {e}", exc_info=True)
            return {"error": str(e)}

    return await call_azure_api(endpoint, key)

# ====== HELPER FUNCTIONS (Your original functions) ======
def load_company_urls(source_file):
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

def add_hyperlink(paragraph, url, text):
    part = paragraph.part
    r_id = part.relate_to(url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
    hyperlink = OxmlElement('w:hyperlink')
    hyperlink.set(qn('r:id'), r_id)
    new_run = OxmlElement('w:r')
    rPr = OxmlElement('w:rPr')
    c = OxmlElement('w:color'); c.set(qn('w:val'), "0563C1"); rPr.append(c)
    u = OxmlElement('w:u'); u.set(qn('w:val'), 'single'); rPr.append(u)
    new_run.append(rPr)
    t = OxmlElement('w:t'); t.text = text; new_run.append(t) # type: ignore
    hyperlink.append(new_run)
    paragraph._p.append(hyperlink)
    return hyperlink

def save_graded_report(df, filename):
    if df.empty:
        logging.warning("No graded data to save.")
        return
    df_sorted = df.sort_values(by='Smart_Bet_Score', ascending=False).head(MAX_RANKING_LIMIT)
    doc = Document()
    doc.add_heading('SBIR Company Grading & Ranking Report', level=0)
    p_date = doc.add_paragraph(f"Report generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    p_date.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_page_break()
    for idx, row in df_sorted.iterrows():
        doc.add_heading(f"Rank #{df_sorted.index.get_loc(idx) + 1}: {row['Company']}", level=1)
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

def safe_get_score(score_dict):
    """Safely retrieves the score from a dictionary, returning 0 if not found."""
    if isinstance(score_dict, dict) and 'score' in score_dict:
        return score_dict['score']
    return 0.0

def safe_get_justification(score_dict):
    """Safely retrieves the justification from a dictionary, returning 'N/A' if not found."""
    if isinstance(score_dict, dict) and 'justification' in score_dict:
        return score_dict['justification']
    return 'N/A'

# ====== MAIN ORCHESTRATOR ======
async def run_grading_process():
    if not os.path.isdir(DOSSIER_FOLDER):
        logging.error(f"Dossier folder does not exist: {DOSSIER_FOLDER}")
        return
        
    company_urls = load_company_urls(COMPANY_DATA_SOURCE_FILE)
    all_graded_results = []
    
    dossier_files = glob.glob(os.path.join(DOSSIER_FOLDER, "*.docx"))
    logging.info(f"Found {len(dossier_files)} dossiers to process.")

    for file_path in dossier_files:
        try:
            doc = Document(file_path)
            if not doc.paragraphs: continue
            
            company_name = doc.paragraphs[0].text.replace("Dossier:", "").strip()
            full_text = "\n".join(p.text for p in doc.paragraphs)
            
            grading_result_dict = await get_ai_grading(full_text)
            
            if not isinstance(grading_result_dict, dict) or 'error' in grading_result_dict:
                logging.error(f"Failed grading for {company_name}: {grading_result_dict.get('error')}")
                continue

            tech_score = safe_get_score(grading_result_dict.get('Technology_Strength'))
            market_score = safe_get_score(grading_result_dict.get('Market_Traction'))
            team_score = safe_get_score(grading_result_dict.get('Team_Experience'))
            dod_score = safe_get_score(grading_result_dict.get('DoD_Alignment'))

            
            smart_bet = (
                (tech_score * GRADING_WEIGHTS['Technology_Strength']) +
                (market_score * GRADING_WEIGHTS['Market_Traction']) +
                (team_score * GRADING_WEIGHTS['Team_Experience']) +
                (dod_score * GRADING_WEIGHTS['DoD_Alignment'])
            )
            
            all_graded_results.append({
                'Company': company_name,
                'Company_URL': company_urls.get(company_name, 'N/A'),
                'Smart_Bet_Score': round(smart_bet, 2),
                'Technology_Strength_Score': tech_score,
                'Technology_Strength_Justification': safe_get_justification(grading_result_dict.get('Technology_Strength')),
                'Market_Traction_Score': market_score,
                'Market_Traction_Justification': safe_get_justification(grading_result_dict.get('Market_Traction')),
                'Team_Experience_Score': team_score,
                'Team_Experience_Justification': safe_get_justification(grading_result_dict.get('Team_Experience')),
                'DoD_Alignment_Score': dod_score,
                'DoD_Alignment_Justification': safe_get_justification(grading_result_dict.get('DoD_Alignment'))
            })
            logging.info(f"Graded {company_name} with score {smart_bet:.2f}")
            await asyncio.sleep(2)
        except Exception as e:
            logging.error(f"Failed processing dossier {file_path}: {e}", exc_info=True)
            
    if all_graded_results:
        df = pd.DataFrame(all_graded_results)
        save_graded_report(df, OUTPUT_FILENAME)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s',
                        handlers=[logging.FileHandler(LOG_FILENAME), logging.StreamHandler()])
    asyncio.run(run_grading_process())
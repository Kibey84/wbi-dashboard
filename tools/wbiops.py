import os
import time
import pandas as pd
from datetime import datetime
import json
import logging
import sys
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
import docx
import asyncio
import re
import inspect

# --- Azure AI Inference Imports ---
from azure.ai.inference.aio import ChatCompletionsClient
from azure.ai.inference.models import SystemMessage, UserMessage
from azure.core.credentials import AzureKeyCredential
from openai import AsyncAzureOpenAI

# --- All Module Imports ---
# NOTE: Make sure to uncomment the scrapers you want to run
# from .dod_sbir_scraper import fetch_dod_sbir_sttr_topics
# from .nasa_sbir_module import fetch_nasa_sbir_opportunities
# from .darpa_module import fetch_darpa_opportunities
# from .arpah_module import fetch_arpah_opportunities
# from .eureka_module import fetch_eureka_opportunities
# from .nsin_module import fetch_nsin_opportunities
# from .nih_sbir_module import fetch_nih_sbir_opportunities
# from .nstxl_module import fetch_nstxl_opportunities
# from .mtec_module import fetch_mtec_opportunities
# from .afwerx_module import fetch_afwerx_opportunities
# from .diu_scraper import fetch_diu_opportunities
# from .socom_baa_module import fetch_socom_opportunities
# from .arl_opportunities_module import fetch_arl_opportunities
# from .nasc_solutions_module import fetch_nasc_opportunities
# from .osti_foa_module import fetch_osti_foas
# from .arpae_scraper import fetch_arpae_opportunities
# from .iarpa_scraper import fetch_iarpa_opportunities
# from .sbir_pipeline_scraper import fetch_sbir_partnership_opportunities
from .sam_gov_api_module import fetch_sam_gov_opportunities

TESTING_MODE = False

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(TOOLS_DIR, "config.json")
COMPANY_KNOWLEDGE_FILE = os.path.join(TOOLS_DIR, "WBI Knowledge.docx")
DB_FILE = os.path.join(TOOLS_DIR, "opportunities.db")
LOG_FILE = os.path.join(TOOLS_DIR, "ai_scraper.log")

COL_URL = 'URL'
COL_IS_NEW = 'Is_New'
COL_RELEVANCE = 'AI Relevance Score'
COL_SOURCE = 'Source'

# Load environment variables
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT")

if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO,
                        handlers=[logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8'),
                                  logging.StreamHandler(sys.stdout)])

# ------------------ DATABASE ------------------
def init_database():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(f'''
            CREATE TABLE IF NOT EXISTS seen_opportunities (
                {COL_URL} TEXT PRIMARY KEY,
                date_seen TEXT NOT NULL
            )''')
        conn.commit()

def load_previous_urls():
    with sqlite3.connect(DB_FILE) as conn:
        return {row[0] for row in conn.execute(f"SELECT {COL_URL} FROM seen_opportunities")}

def save_new_urls(df):
    if df.empty or COL_URL not in df or COL_IS_NEW not in df:
        return
    new_df = df[df[COL_IS_NEW]].copy()
    if new_df.empty:
        return
    new_df['date_seen'] = datetime.now().isoformat()
    with sqlite3.connect(DB_FILE) as conn:
        new_df[[COL_URL, 'date_seen']].to_sql('seen_opportunities', conn, if_exists='append', index=False)

# ------------------ COMPANY KNOWLEDGE ------------------
def load_company_knowledge():
    doc = docx.Document(COMPANY_KNOWLEDGE_FILE)
    return '\n'.join(para.text for para in doc.paragraphs)

# ------------------ SCRAPER CONFIG ------------------
def load_scraper_config():
    with open(CONFIG_FILE, 'r') as f:
        config = json.load(f)
    valid = []
    for scraper in config.get('scrapers', []):
        func_name = scraper.get('function')
        if not func_name:
            continue
        if hasattr(sys.modules[__name__], func_name):
            scraper['function'] = getattr(sys.modules[__name__], func_name)
            valid.append(scraper)
    return valid

def run_scraper_task(scraper_config):
    name = scraper_config['name']
    try:
        target_func = scraper_config['function']
        valid_params = inspect.signature(target_func).parameters
        raw_kwargs = {arg: val for arg, val in scraper_config.get('args', {}).items()}
        if name == "SBIR Partnerships":
            raw_kwargs['testing_mode'] = TESTING_MODE
        filtered_kwargs = {key: val for key, val in raw_kwargs.items() if key in valid_params}
        data = target_func(**filtered_kwargs)
        for item in data:
            item[COL_SOURCE] = name
        return data, None
    except Exception as e:
        logging.error(f"Scraper failed for {name}: {e}", exc_info=True)
        return [], e

# ------------------ AI ANALYSIS (REFACTORED with stable 'openai' library) ------------------
async def analyze_opportunity_with_ai(opportunity, knowledge, client: AsyncAzureOpenAI):
    """Async analysis function that uses the stable openai client."""
    text = f"""
    Title: {opportunity.get('Title', '')}
    Description: {opportunity.get('Description', '')}
    Set-Aside: {opportunity.get('SetAside', 'N/A')}
    NAICS: {opportunity.get('NAICS', 'N/A')}
    Classification: {opportunity.get('Classification', 'N/A')}
    POC: {json.dumps(opportunity.get('POC', []), indent=2) if opportunity.get('POC') else "N/A"}
    """
    if not text.strip():
        return None 

    user_prompt = f"""
    WBI CAPABILITIES: --- {knowledge} ---
    OPPORTUNITY DATA:
    {text}
    TASK:
    Assess this opportunity for WBI relevance. Return ONLY valid JSON with keys:
    "relevance_score" (0-1), "justification", "related_experience", 
    "funding_assessment", "suggested_internal_lead".
    """

    try:
        assert AZURE_OPENAI_DEPLOYMENT is not None
        response = await client.chat.completions.create(
            model=AZURE_OPENAI_DEPLOYMENT,
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.7,
            max_tokens=500,
        )
        
        content = response.choices[0].message.content if response.choices and response.choices[0].message else ""
        if not content:
            return None

        match = re.search(r'\{.*\}', content, re.DOTALL)
        if match:
            result_json = json.loads(match.group(0))
            result_json['original_opportunity'] = opportunity
            return result_json
        else:
            logging.error(f"No JSON found in AI response: {content}")
            return None

    except Exception as e:
        logging.error(f"AI call failed for opportunity {opportunity.get('Title')}: {e}", exc_info=False)
        return None


# ------------------ MAIN PIPELINE (REFACTORED WITH BATCHING) ------------------
def run_wbi_pipeline(log):
    if not all([AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY, AZURE_OPENAI_DEPLOYMENT]):
        log.append({"text": "❌ ERROR: Azure AI environment variables are not set. Halting pipeline."})
        logging.error("Azure AI environment variables are not set.")
        return pd.DataFrame(), pd.DataFrame()

    start = time.time()
    log.append({"text": "🚀 Starting Pipeline..."})
    if TESTING_MODE:
        log.append({"text": "--- TESTING MODE ---"})

    init_database()
    knowledge = load_company_knowledge()
    seen = load_previous_urls()

    config = load_scraper_config()
    sbir_partners = []
    sbir_conf = next((c for c in config if c['name'] == 'SBIR Partnerships'), None)
    if sbir_conf:
        sbir_partners, _ = run_scraper_task(sbir_conf)

    direct_scrapers = [c for c in config if c['name'] != 'SBIR Partnerships' and c.get('enabled', True)]
    all_opps, failed = [], []

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(run_scraper_task, c): c['name'] for c in direct_scrapers}
        for future in as_completed(futures):
            name = futures[future]
            try:
                data, error = future.result()
                if error:
                    failed.append(name)
                else:
                    all_opps.extend(data)
            except Exception as e:
                failed.append(name)

    log.append({"text": f"Found {len(all_opps)} opportunities. Starting AI analysis..."})

    # --- EFFICIENT ASYNC AI ANALYSIS WITH BATCHING ---
    relevant = []
    BATCH_SIZE = 20 

    async def main_analysis():
        assert AZURE_OPENAI_ENDPOINT is not None
        assert AZURE_OPENAI_KEY is not None
        
        client = AsyncAzureOpenAI(
            azure_endpoint=AZURE_OPENAI_ENDPOINT,
            api_key=AZURE_OPENAI_KEY,
            api_version="2024-02-01"
        )

        for i in range(0, len(all_opps), BATCH_SIZE):
            batch_opps = all_opps[i:i + BATCH_SIZE]
            log.append({"text": f"Analyzing batch {i//BATCH_SIZE + 1} of {len(all_opps)//BATCH_SIZE + 1}..."})
            
            tasks = [analyze_opportunity_with_ai(opp, knowledge, client) for opp in batch_opps]
            results = await asyncio.gather(*tasks)

            for result in results:
                if result and result.get('relevance_score', 0) >= 0.7:
                    opp = result.pop('original_opportunity') 
                    opp.update(result)
                    relevant.append(opp)
            
            await asyncio.sleep(1)

    asyncio.run(main_analysis())
    # --- END OF BATCHING BLOCK ---

    df_opps = pd.DataFrame(relevant)
    if not df_opps.empty:
        df_opps[COL_IS_NEW] = df_opps[COL_URL].apply(lambda url: url not in seen)
        save_new_urls(df_opps)

    elapsed = time.time() - start
    log.append({"text": f"Pipeline finished in {elapsed:.2f} sec."})
    if failed:
        log.append({"text": f"❗ Failed scrapers: {', '.join(failed)}"})

    return df_opps, pd.DataFrame(sbir_partners)

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

from .dod_sbir_scraper import fetch_dod_sbir_sttr_topics
from .nasa_sbir_module import fetch_nasa_sbir_opportunities
from .darpa_module import fetch_darpa_opportunities
from .arpah_module import fetch_arpah_opportunities
from .eureka_module import fetch_eureka_opportunities
from .nsin_module import fetch_nsin_opportunities
from .nih_sbir_module import fetch_nih_sbir_opportunities
from .nstxl_module import fetch_nstxl_opportunities
from .mtec_module import fetch_mtec_opportunities
from .afwerx_module import fetch_afwerx_opportunities
from .diu_scraper import fetch_diu_opportunities
from .socom_baa_module import fetch_socom_opportunities
from .arl_opportunities_module import fetch_arl_opportunities
from .nasc_solutions_module import fetch_nasc_opportunities
from .osti_foa_module import fetch_osti_foas
from .arpae_scraper import fetch_arpae_opportunities
from .iarpa_scraper import fetch_iarpa_opportunities
from .sbir_pipeline_scraper import fetch_sbir_partnership_opportunities
from .sam_gov_api_module import fetch_sam_gov_opportunities

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

from azure.ai.inference.aio import ChatCompletionsClient
from azure.ai.inference.models import SystemMessage, UserMessage
from azure.core.credentials import AzureKeyCredential

TESTING_MODE = False

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(TOOLS_DIR, "config.json")
COMPANY_KNOWLEDGE_FILE = os.path.join(TOOLS_DIR, "WBI Knowledge.docx")
DB_FILE = os.path.join(TOOLS_DIR, "opportunities.db")
LOG_FILE = os.path.join(TOOLS_DIR, "ai_scraper.log")

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0.0.0 Safari/537.36 WBiOpsScraper/3.3"}

if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, handlers=[logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8'), logging.StreamHandler(sys.stdout)])

COL_URL = 'URL'
COL_IS_NEW = 'Is_New'
COL_RELEVANCE = 'AI Relevance Score'
COL_SOURCE = 'Source'

def init_database():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(f'''CREATE TABLE IF NOT EXISTS seen_opportunities ({COL_URL} TEXT PRIMARY KEY, date_seen TEXT NOT NULL)''')
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

def load_company_knowledge():
    doc = docx.Document(COMPANY_KNOWLEDGE_FILE)
    return '\n'.join(para.text for para in doc.paragraphs)

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

def create_driver(headless=True):
    options = Options()
    if headless:
        options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument(f'user-agent={HEADERS["User-Agent"]}')
    return webdriver.Chrome(options=options)

def run_scraper_task(scraper_config):
    name = scraper_config['name']
    driver = None
    try:
        kwargs = {arg: HEADERS if val == "HEADERS" else val for arg, val in scraper_config.get('args', {}).items()}
        if scraper_config.get('requires_driver'):
            driver = create_driver()
            if not driver:
                raise RuntimeError(f"Failed to initialize driver for {name}")
            driver_param = scraper_config.get('driver_param_name', 'driver')
            kwargs[driver_param] = driver
        if name == "SBIR Partnerships":
            kwargs['testing_mode'] = TESTING_MODE
        data = scraper_config['function'](**kwargs)
        for item in data:
            item[COL_SOURCE] = name
        return data, None
    except Exception as e:
        logging.error(f"Scraper failed for {name}: {e}", exc_info=True)
        return [], e
    finally:
        if driver:
            driver.quit()

async def call_azure_ai_async(system_prompt, user_prompt):
    endpoint = os.getenv("AZURE_AI_ENDPOINT")
    key = os.getenv("AZURE_AI_KEY")
    if not endpoint or not key:
        logging.error("Missing Azure AI environment variables.")
        return None
    try:
        client = ChatCompletionsClient(endpoint=endpoint, credential=AzureKeyCredential(key))
        result = await client.complete(
            deployment_name="gpt-4",
            messages=[
                SystemMessage(content=system_prompt),
                UserMessage(content=user_prompt)
            ],
            max_tokens=1024,
            temperature=0.2
        )
        return result.choices[0].message.content.strip()
    except Exception as e:
        logging.error(f"Azure AI failed: {e}", exc_info=True)
        return None

def analyze_opportunity_with_ai(opportunity, knowledge):
    text = f"Title: {opportunity.get('Title', '')}\n\nDescription: {opportunity.get('Description', '')}"
    if not text.strip():
        return {"relevance_score": 0}
    system_prompt = "You are a business analyst for WBI specializing in defense opportunities."
    user_prompt = f"""
    WBI CAPABILITIES: --- {knowledge} ---
    OPPORTUNITY TEXT: --- {text} ---
    TASK: Analyze this opportunity for relevance to WBI. Reply ONLY with JSON having keys: "relevance_score", "justification", "related_experience", "funding_assessment", "suggested_internal_lead".
    """
    loop = asyncio.get_event_loop()
    response = loop.run_until_complete(call_azure_ai_async(system_prompt, user_prompt))
    if not response:
        return {"relevance_score": 0}
    try:
        match = re.search(r'\{.*\}', response, re.DOTALL)
        if match:
            return json.loads(match.group(0))
    except json.JSONDecodeError:
        logging.error(f"Invalid JSON in AI response: {response}")
    return {"relevance_score": 0}

def find_partners_with_ai(opportunity, partners, knowledge):
    if not partners:
        return []
    partners_text = "\n".join(f"- {p['company_name']}: {p['project_title']}" for p in partners)
    system_prompt = "You are a WBI analyst. Recommend partner companies for an opportunity."
    user_prompt = f"""
    OPPORTUNITY: --- Title: {opportunity.get('Title', '')} --- Description: {opportunity.get('Description', '')} ---
    PARTNERS: --- {partners_text} ---
    TASK: Recommend up to 3 partners as JSON: {{ "suggested_partners": [{{"partner_company": "", "reasoning": ""}}] }}
    """
    loop = asyncio.get_event_loop()
    response = loop.run_until_complete(call_azure_ai_async(system_prompt, user_prompt))
    if not response:
        return []
    try:
        match = re.search(r'\{.*\}', response, re.DOTALL)
        if match:
            return json.loads(match.group(0)).get('suggested_partners', [])
    except json.JSONDecodeError:
        logging.error(f"Invalid JSON in partner AI response: {response}")
    return []

def run_wbi_pipeline(log):
    start = time.time()
    log.append({"text": "🚀 Starting Pipeline..."})
    if TESTING_MODE:
        log.append({"text": "--- TESTING MODE ---"})
    init_database()
    knowledge = load_company_knowledge()
    config = load_scraper_config()
    seen = load_previous_urls()
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
    if TESTING_MODE and len(all_opps) > 5:
        all_opps = all_opps[:5]
    relevant = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(analyze_opportunity_with_ai, o, knowledge): o for o in all_opps}
        for future in as_completed(futures):
            opp = futures[future]
            try:
                result = future.result()
                if result.get('relevance_score', 0) >= 0.7:
                    opp.update(result)
                    relevant.append(opp)
            except Exception as e:
                logging.error(f"Error on AI analysis: {e}")
    df_opps = pd.DataFrame(relevant)
    if not df_opps.empty:
        df_opps[COL_IS_NEW] = df_opps[COL_URL].apply(lambda url: url not in seen)
    df_matches = pd.DataFrame()
    if relevant and sbir_partners:
        matches = []
        for opp in relevant:
            partners = find_partners_with_ai(opp, sbir_partners, knowledge)
            if partners:
                matches.append({
                    "Direct Opportunity Title": opp.get('Title'),
                    "Direct Opportunity URL": opp.get('URL'),
                    "Suggested Partners": "\n".join(f"- {p['partner_company']}: {p['reasoning']}" for p in partners)
                })
        if matches:
            df_matches = pd.DataFrame(matches)
    elapsed = time.time() - start
    log.append({"text": f"Pipeline finished in {elapsed:.2f} sec."})
    if failed:
        log.append({"text": f"❗ Failed scrapers: {', '.join(failed)}"})
    return df_opps, df_matches

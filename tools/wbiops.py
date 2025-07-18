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
from dotenv import load_dotenv
import asyncio
import re

from .dod_sbir_scraper import fetch_dod_sbir_sttr_topics
from .nasa_sbir_module import fetch_nasa_sbir_opportunities
from .darpa_module import fetch_darpa_opportunities
from .arpah_module import fetch_arpah_opportunities
from .eureka_module import fetch_eureka_opportunities
from .nsin_module import fetch_nsin_opportunities
from .nih_sbir_module import fetch_nih_sbir_opportunities
from .tradewinds_module import fetch_tradewinds_opportunities
from .nstxl_module import fetch_nstxl_opportunities
from .mtec_module import fetch_mtec_opportunities
from .afwerx_module import fetch_afwerx_opportunities
from .grantsgov_module import fetch_grantsgov_opportunities
from .diu_scraper import fetch_diu_opportunities
from .socom_baa_module import fetch_socom_opportunities
from .arl_opportunities_module import fetch_arl_opportunities
from .nasc_solutions_module import fetch_nasc_opportunities
from .osti_foa_module import fetch_osti_foas
from .arpae_scraper import fetch_arpae_opportunities
from .iarpa_scraper import fetch_iarpa_opportunities
from .sbir_pipeline_scraper import fetch_sbir_partnership_opportunities

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from openpyxl.utils import get_column_letter
from openpyxl.styles import Font, Alignment, PatternFill
from azure.ai.inference.aio import ChatCompletionsClient
from azure.ai.inference.models import SystemMessage, UserMessage
from azure.core.credentials import AzureKeyCredential

TESTING_MODE = True

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "samgovkey.env"))

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(TOOLS_DIR, "config.json")
COMPANY_KNOWLEDGE_FILE = os.path.join(TOOLS_DIR, "WBI Knowledge.docx")
DB_FILE = os.path.join(TOOLS_DIR, "opportunities.db")
LOG_FILE = os.path.join(TOOLS_DIR, "ai_scraper.log")

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0.0.0 Safari/537.36 WBiOpsScraper/3.3"}
COL_URL = 'URL'
COL_IS_NEW = 'Is_New'
COL_RELEVANCE = 'AI Relevance Score'
COL_TITLE = 'Title'
COL_DESC = 'Description'
COL_SOURCE = 'Source'

if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s',
                        handlers=[logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8'), logging.StreamHandler(sys.stdout)])

def init_database():
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute(f'''CREATE TABLE IF NOT EXISTS seen_opportunities ({COL_URL} TEXT PRIMARY KEY NOT NULL, date_seen TEXT NOT NULL)''')
            conn.commit()
    except sqlite3.Error as e:
        logging.error(f"Database initialization failed: {e}", exc_info=True)
        raise

def load_previous_urls():
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT {COL_URL} FROM seen_opportunities")
            return {row[0] for row in cursor.fetchall()}
    except sqlite3.Error as e:
        logging.error(f"Could not load previous URLs from database: {e}", exc_info=True)
        return set()

def save_new_urls(df):
    if df.empty or COL_URL not in df.columns or COL_IS_NEW not in df.columns: return
    new_opps_df = df[df[COL_IS_NEW]].copy()
    if new_opps_df.empty: return
    urls_to_save = new_opps_df[[COL_URL]]
    urls_to_save['date_seen'] = datetime.now().isoformat()
    try:
        with sqlite3.connect(DB_FILE) as conn:
            urls_to_save.to_sql('seen_opportunities', conn, if_exists='append', index=False)
        logging.info(f"Successfully saved {len(urls_to_save)} new opportunity URLs.")
    except sqlite3.Error as e:
        logging.error(f"Failed to save new URLs to the database: {e}", exc_info=True)

def load_company_knowledge():
    try:
        doc = docx.Document(COMPANY_KNOWLEDGE_FILE)
        return '\n'.join([para.text for para in doc.paragraphs])
    except Exception as e:
        logging.error(f"FATAL: Could not read '{COMPANY_KNOWLEDGE_FILE}': {e}", exc_info=True)
        raise e

def load_scraper_config():
    try:
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)

        valid_scrapers = []
        for scraper in config.get('scrapers', []):
            function_name = scraper.get('function')
            if not function_name:
                logging.warning(f"Scraper '{scraper.get('name')}' is missing a 'function' name in config.json. Skipping.")
                continue

            if hasattr(sys.modules[__name__], function_name):
                scraper['function'] = getattr(sys.modules[__name__], function_name)
                valid_scrapers.append(scraper)
            else:
                logging.warning(f"Could not find function '{function_name}' for scraper '{scraper.get('name')}'. Skipping.")

        return valid_scrapers
    except Exception as e:
        logging.error(f"FATAL: Could not load or parse '{CONFIG_FILE}': {e}", exc_info=True)
        return []

def create_driver(headless_mode=True):
    options = Options()
    if headless_mode:
        options.add_argument('--headless=new')
    options.add_argument('--disable-gpu')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument(f'user-agent={HEADERS["User-Agent"]}')
    try:
        return webdriver.Chrome(options=options)
    except Exception as e:
        logging.error(f"Failed to create WebDriver: {e}", exc_info=True)
        return None

def run_scraper_task(scraper_config):
    scraper_name = scraper_config['name']
    logging.info(f"🔩 Starting task for: {scraper_name}")
    driver = None
    try:
        call_kwargs = {}
        for arg, val in scraper_config.get("args", {}).items():
            call_kwargs[arg] = HEADERS if val == "HEADERS" else val
        if scraper_config.get("requires_driver"):
            driver = create_driver()
            if not driver:
                raise RuntimeError("Could not create WebDriver.")
            driver_param_name = scraper_config.get("driver_param_name", "driver")
            call_kwargs[driver_param_name] = driver
        if scraper_name == "SBIR Partnerships":
            call_kwargs['testing_mode'] = TESTING_MODE
        scraped_data = scraper_config["function"](**call_kwargs)
        for item in scraped_data:
            item[COL_SOURCE] = scraper_name
        logging.info(f"✅ {scraper_name} finished, found {len(scraped_data)} items.")
        return scraped_data, None
    except Exception as e:
        logging.error(f"❌ Error during scraping for {scraper_name}: {e}", exc_info=True)
        return [], e
    finally:
        if driver:
            driver.quit()

async def call_azure_ai_async(system_prompt, user_prompt):
    endpoint = os.getenv("AZURE_AI_ENDPOINT")
    key = os.getenv("AZURE_AI_KEY")
    if not endpoint or not key:
        logging.error("AZURE_AI_ENDPOINT or AZURE_AI_KEY is missing.")
        return None

    try:
        client = ChatCompletionsClient(endpoint=endpoint, credential=AzureKeyCredential(key))
        response = await client.complete(
            deployment_name="gpt-4",
            messages=[
                SystemMessage(content=system_prompt),
                UserMessage(content=user_prompt)
            ],
            max_tokens=1024,
            temperature=0.2
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logging.error(f"Azure AI call failed: {e}", exc_info=True)
        return None

def analyze_opportunity_with_ai(opportunity, company_knowledge):
    opportunity_text = f"Title: {opportunity.get('Title', '')}\n\nDescription: {opportunity.get('Description', '')}"
    if not opportunity_text.strip():
        return {"relevance_score": 0}

    system_prompt = "You are a specialized business development analyst for WBI, a problem-solver for the DoD."
    user_prompt = f"""
    COMPANY CAPABILITIES: --- {company_knowledge} ---
    OPPORTUNITY TEXT: --- {opportunity_text[:15000]} ---
    YOUR TASK: Analyze the opportunity based on our portfolio. Provide ONLY a valid JSON object with keys "relevance_score", "justification", "related_experience", "funding_assessment", "suggested_internal_lead".
    """
    loop = asyncio.get_event_loop()
    ai_response = loop.run_until_complete(call_azure_ai_async(system_prompt, user_prompt))
    if not ai_response:
        return {"relevance_score": 0}

    try:
        match = re.search(r'\{.*\}', ai_response, re.DOTALL)
        if match:
            return json.loads(match.group(0))
    except json.JSONDecodeError:
        logging.error(f"Failed to decode JSON from AI response: {ai_response}")
    return {"relevance_score": 0}

def find_partners_with_ai(direct_opportunity, sbir_partners_list, company_knowledge):
    if not sbir_partners_list:
        return []
    logging.info(f"🤖 Searching for SBIR partners for opportunity: '{direct_opportunity.get('Title', 'N/A')[:50]}'")

    partners_text = "\n".join([f"- Company: {p['company_name']}, Project: {p['project_title']}" for p in sbir_partners_list])
    system_prompt = "You are a strategic analyst for WBI. Find potential small business partners for a new opportunity."
    user_prompt = f"""
    NEW OPPORTUNITY: --- Title: {direct_opportunity.get('Title', '')} Description: {direct_opportunity.get('Description', '')} ---
    POTENTIAL PARTNERS: --- {partners_text} ---
    YOUR TASK: Identify up to 3 companies whose projects make them a strong partner. Provide ONLY a valid JSON object like: {{ "suggested_partners": [ {{ "partner_company": "<Company Name>", "reasoning": "<Reason>" }} ] }}
    """

    loop = asyncio.get_event_loop()
    ai_response = loop.run_until_complete(call_azure_ai_async(system_prompt, user_prompt))
    if not ai_response:
        return []

    try:
        match = re.search(r'\{.*\}', ai_response, re.DOTALL)
        if match:
            analysis = json.loads(match.group(0))
            return analysis.get("suggested_partners", [])
    except json.JSONDecodeError:
        logging.error(f"Failed to decode JSON from matchmaking AI response: {ai_response}")
    return []

def run_wbi_pipeline(log):
    start_time = time.time()
    log.append({"text": "🚀 Starting Integrated Business Development Pipeline..."})
    if TESTING_MODE:
        log.append({"text": "--- RUNNING IN TESTING MODE ---"})

    init_database()
    company_knowledge = load_company_knowledge()
    all_scraper_configs = load_scraper_config()
    previously_seen_urls = load_previous_urls()

    sbir_partner_list = []
    sbir_config = next((c for c in all_scraper_configs if c['name'] == 'SBIR Partnerships'), None)
    if sbir_config:
        sbir_data, error = run_scraper_task(sbir_config)
        if not error:
            sbir_partner_list = sbir_data
    else:
        log.append({"text": "⚠️ Could not find 'SBIR Partnerships' configuration. Skipping matchmaking."})

    direct_scrapers = [s for s in all_scraper_configs if s.get("name") != "SBIR Partnerships" and s.get("enabled", True)]
    all_raw_opps = []
    failed_scrapers = []
    if direct_scrapers:
        log.append({"text": f"--- Running {len(direct_scrapers)} direct opportunity scrapers ---"})
        with ThreadPoolExecutor(max_workers=8) as executor:
            future_to_scraper = {executor.submit(run_scraper_task, config): config for config in direct_scrapers}
            for future in as_completed(future_to_scraper):
                scraper_name = future_to_scraper[future]['name']
                try:
                    data, error = future.result()
                    if error:
                        failed_scrapers.append(scraper_name)
                    else:
                        all_raw_opps.extend(data)
                except Exception as e:
                    logging.error(f"Critical error for '{scraper_name}': {e}", exc_info=True)
                    failed_scrapers.append(scraper_name)

    log.append({"text": f"\n🧠 Found {len(all_raw_opps)} total items. Starting AI analysis..."})
    if TESTING_MODE and len(all_raw_opps) > 5:
        log.append({"text": f"Testing mode: Analyzing only 5 of {len(all_raw_opps)} opportunities."})
        all_raw_opps = all_raw_opps[:5]

    relevant_opps = []
    if all_raw_opps:
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_opp = {executor.submit(analyze_opportunity_with_ai, opp, company_knowledge): opp for opp in all_raw_opps}
            for future in as_completed(future_to_opp):
                original_opp = future_to_opp[future]
                try:
                    ai_analysis = future.result()
                    if ai_analysis and ai_analysis.get('relevance_score', 0) >= 0.7:
                        original_opp.update(ai_analysis)
                        relevant_opps.append(original_opp)
                except Exception as e:
                    logging.error(f"Error processing AI result for '{original_opp.get('Title')}': {e}", exc_info=True)

    df_direct_opps = pd.DataFrame(relevant_opps) if relevant_opps else pd.DataFrame()
    if not df_direct_opps.empty:
        df_direct_opps[COL_IS_NEW] = df_direct_opps[COL_URL].apply(lambda url: url not in previously_seen_urls)

    df_matchmaking = pd.DataFrame()
    if relevant_opps and sbir_partner_list:
        log.append({"text": f"\n💞 Starting Strategic Matchmaking..."})
        matchmaking_results = []
        for opp in relevant_opps:
            suggested_partners = find_partners_with_ai(opp, sbir_partner_list, company_knowledge)
            if suggested_partners:
                matchmaking_results.append({
                    "Direct Opportunity Title": opp.get('Title'),
                    "Direct Opportunity URL": opp.get('URL'),
                    "Suggested Partners": "\n".join([f"- {p['partner_company']}: {p['reasoning']}" for p in suggested_partners])
                })
        if matchmaking_results:
            df_matchmaking = pd.DataFrame(matchmaking_results)

    end_time = time.time()
    log.append({"text": f"--- Pipeline finished in {end_time - start_time:.2f} seconds. ---"})
    if failed_scrapers:
        log.append({"text": f"❗️ Failed scrapers: {', '.join(failed_scrapers)}"})

    return df_direct_opps, df_matchmaking

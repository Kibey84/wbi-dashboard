import os
import time
import pandas as pd
from datetime import datetime, timedelta
import json
import logging
import sys
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
import docx
import asyncio
import re
import inspect
from typing import Dict, List, Any, Optional, Tuple
import html
from contextlib import asynccontextmanager

# --- Configuration Constants ---
TESTING_MODE = os.getenv("TESTING_MODE", "false").lower() == "true"
BATCH_SIZE = int(os.getenv("AI_BATCH_SIZE", "5"))
API_DELAY = float(os.getenv("AI_API_DELAY", "1.0"))
MAX_RETRIES = int(os.getenv("AI_MAX_RETRIES", "3"))
MAX_WORKERS = int(os.getenv("SCRAPER_MAX_WORKERS", "8"))
AI_TIMEOUT = int(os.getenv("AI_TIMEOUT", "30"))
RELEVANCE_THRESHOLD = float(os.getenv("RELEVANCE_THRESHOLD", "0.7"))
DATABASE_TIMEOUT = int(os.getenv("DATABASE_TIMEOUT", "30"))

# --- File Paths ---
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(TOOLS_DIR, "config.json")
COMPANY_KNOWLEDGE_FILE = os.path.join(TOOLS_DIR, "WBI Knowledge.docx")
DB_FILE = os.path.join(TOOLS_DIR, "opportunities.db")
LOG_FILE = os.path.join(TOOLS_DIR, "ai_scraper.log")

# --- Column Constants ---
COL_URL = 'URL'
COL_IS_NEW = 'Is_New'
COL_RELEVANCE = 'AI Relevance Score'
COL_SOURCE = 'Source'

# --- Environment Variables ---
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT")
SAM_GOV_API_KEY = os.getenv("SAM_GOV_API_KEY")

# --- Azure AI Imports ---
from openai import AsyncAzureOpenAI

# --- Module Imports ---
try:
    from . import FETCH_FUNCTIONS
except ImportError as e:
    logging.error(f"Failed to import FETCH_FUNCTIONS: {e}")
    FETCH_FUNCTIONS = {}

# --- Logging Configuration ---
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )

logger = logging.getLogger(__name__)

# Log configuration status
logger.info(f"SAM_GOV_API_KEY present: {'YES' if bool(SAM_GOV_API_KEY) else 'NO'}")
logger.info(f"Testing mode: {'ENABLED' if TESTING_MODE else 'DISABLED'}")
logger.info(f"AI batch size: {BATCH_SIZE}, API delay: {API_DELAY}s")

def validate_configuration() -> None:
    """Validate required configuration at startup."""
    required_vars = [
        ("AZURE_OPENAI_ENDPOINT", AZURE_OPENAI_ENDPOINT),
        ("AZURE_OPENAI_KEY", AZURE_OPENAI_KEY),
        ("AZURE_OPENAI_DEPLOYMENT", AZURE_OPENAI_DEPLOYMENT)
    ]
    
    missing = [name for name, value in required_vars if not value]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
    
    # Validate file dependencies
    if not os.path.exists(CONFIG_FILE):
        raise FileNotFoundError(f"Configuration file not found: {CONFIG_FILE}")
    
    if not os.path.exists(COMPANY_KNOWLEDGE_FILE):
        raise FileNotFoundError(f"Company knowledge file not found: {COMPANY_KNOWLEDGE_FILE}")
    
    logger.info("✅ WBI pipeline configuration validated successfully")

def get_validated_azure_config() -> Tuple[str, str, str]:
    """Get validated Azure configuration, raising error if any are None."""
    if not AZURE_OPENAI_ENDPOINT:
        raise RuntimeError("AZURE_OPENAI_ENDPOINT is not configured")
    if not AZURE_OPENAI_KEY:
        raise RuntimeError("AZURE_OPENAI_KEY is not configured")
    if not AZURE_OPENAI_DEPLOYMENT:
        raise RuntimeError("AZURE_OPENAI_DEPLOYMENT is not configured")
    
    return AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY, AZURE_OPENAI_DEPLOYMENT

# ------------------ DATABASE OPERATIONS ------------------

class DatabaseManager:
    """Enhanced database manager with connection pooling and error handling."""
    
    def __init__(self, db_file: str):
        self.db_file = db_file
        self._init_database()
    
    def _init_database(self) -> None:
        """Initialize database with proper error handling."""
        try:
            with sqlite3.connect(self.db_file, timeout=DATABASE_TIMEOUT) as conn:
                conn.execute(f'''
                    CREATE TABLE IF NOT EXISTS seen_opportunities (
                        {COL_URL} TEXT PRIMARY KEY,
                        date_seen TEXT NOT NULL,
                        source TEXT,
                        last_updated TEXT
                    )''')
                conn.execute('''
                    CREATE INDEX IF NOT EXISTS idx_date_seen ON seen_opportunities(date_seen);
                ''')
                conn.commit()
                logger.info("Database initialized successfully")
        except sqlite3.Error as e:
            logger.error(f"Database initialization failed: {e}")
            raise
    
    def load_previous_urls(self) -> set:
        """Load previously seen URLs with error handling."""
        try:
            with sqlite3.connect(self.db_file, timeout=DATABASE_TIMEOUT) as conn:
                cursor = conn.execute(f"SELECT {COL_URL} FROM seen_opportunities")
                urls = {row[0] for row in cursor.fetchall()}
                logger.info(f"Loaded {len(urls)} previously seen URLs")
                return urls
        except sqlite3.Error as e:
            logger.error(f"Failed to load previous URLs: {e}")
            return set()
    
    def save_new_urls(self, df: pd.DataFrame) -> None:
        """Save new URLs with enhanced error handling and deduplication."""
        if df.empty or COL_URL not in df or COL_IS_NEW not in df:
            return
        
        new_df = df[df[COL_IS_NEW]].copy()
        if new_df.empty:
            return
        
        current_time = datetime.now().isoformat()
        new_df['date_seen'] = current_time
        new_df['last_updated'] = current_time
        
        try:
            with sqlite3.connect(self.db_file, timeout=DATABASE_TIMEOUT) as conn:
                # Use INSERT OR REPLACE to handle duplicates
                for _, row in new_df.iterrows():
                    conn.execute('''
                        INSERT OR REPLACE INTO seen_opportunities 
                        (URL, date_seen, source, last_updated) 
                        VALUES (?, ?, ?, ?)
                    ''', (
                        row[COL_URL], 
                        current_time, 
                        row.get(COL_SOURCE, 'Unknown'),
                        current_time
                    ))
                conn.commit()
                logger.info(f"Saved {len(new_df)} new URLs to database")
        except sqlite3.Error as e:
            logger.error(f"Failed to save new URLs: {e}")
    
    def cleanup_old_entries(self, days_old: int = 30) -> None:
        """Clean up old database entries."""
        try:
            cutoff_date = (datetime.now() - timedelta(days=days_old)).isoformat()
            with sqlite3.connect(self.db_file, timeout=DATABASE_TIMEOUT) as conn:
                cursor = conn.execute(
                    "DELETE FROM seen_opportunities WHERE date_seen < ?", 
                    (cutoff_date,)
                )
                deleted_count = cursor.rowcount
                conn.commit()
                if deleted_count > 0:
                    logger.info(f"Cleaned up {deleted_count} old database entries")
        except sqlite3.Error as e:
            logger.error(f"Database cleanup failed: {e}")

# Initialize database manager
db_manager = DatabaseManager(DB_FILE)

# ------------------ COMPANY KNOWLEDGE ------------------

def load_company_knowledge() -> str:
    """Load company knowledge with enhanced error handling."""
    try:
        if not os.path.exists(COMPANY_KNOWLEDGE_FILE):
            logger.error(f"Company knowledge file not found: {COMPANY_KNOWLEDGE_FILE}")
            return "Company knowledge not available."
        
        doc = docx.Document(COMPANY_KNOWLEDGE_FILE)
        content = '\n'.join(para.text for para in doc.paragraphs if para.text.strip())
        
        if not content.strip():
            logger.warning("Company knowledge file appears to be empty")
            return "No company knowledge available."
        
        logger.info(f"Loaded company knowledge: {len(content)} characters")
        return content
        
    except Exception as e:
        logger.error(f"Failed to load company knowledge: {e}")
        return "Error loading company knowledge."

# ------------------ SCRAPER CONFIGURATION ------------------

def load_scraper_config() -> List[Dict[str, Any]]:
    """Load and validate scraper configuration with enhanced error handling."""
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except FileNotFoundError:
        logger.error(f"Configuration file not found: {CONFIG_FILE}")
        return []
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in configuration file: {e}")
        return []
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        return []
    
    if not FETCH_FUNCTIONS:
        logger.error("No FETCH_FUNCTIONS available - check tools/__init__.py")
        return []
    
    valid_scrapers = []
    invalid_scrapers = []
    
    for scraper in config.get('scrapers', []):
        scraper_name = scraper.get('name', 'Unknown')
        func_name = scraper.get('function')
        
        if not func_name:
            logger.warning(f"Scraper '{scraper_name}' missing function name - skipping")
            invalid_scrapers.append(scraper_name)
            continue
        
        if func_name not in FETCH_FUNCTIONS:
            logger.warning(f"Function '{func_name}' for scraper '{scraper_name}' not found - skipping")
            invalid_scrapers.append(scraper_name)
            continue
        
        # Validate function signature
        try:
            target_func = FETCH_FUNCTIONS[func_name]
            signature = inspect.signature(target_func)
            scraper['function'] = target_func
            scraper['signature'] = signature
            valid_scrapers.append(scraper)
        except Exception as e:
            logger.warning(f"Invalid function '{func_name}' for scraper '{scraper_name}': {e}")
            invalid_scrapers.append(scraper_name)
    
    if valid_scrapers:
        logger.info(f"Loaded {len(valid_scrapers)} valid scrapers: {', '.join(s['name'] for s in valid_scrapers)}")
    else:
        logger.error("No valid scrapers loaded - pipeline will not function")
    
    if invalid_scrapers:
        logger.warning(f"Skipped {len(invalid_scrapers)} invalid scrapers: {', '.join(invalid_scrapers)}")
    
    return valid_scrapers

def run_scraper_task(scraper_config: Dict[str, Any]) -> Tuple[List[Dict], Optional[Exception]]:
    """Enhanced scraper task with better error handling and validation."""
    name = scraper_config['name']
    logger.info(f"▶️ Starting scraper: {name}")
    
    try:
        target_func = scraper_config['function']
        signature = scraper_config.get('signature')
        
        if not signature:
            signature = inspect.signature(target_func)
        
        # Prepare arguments
        raw_kwargs = scraper_config.get('args', {}).copy()
        
        # Add special parameters
        if name == "SBIR Partnerships":
            raw_kwargs['testing_mode'] = TESTING_MODE
        
        # Filter to only valid parameters
        valid_params = set(signature.parameters.keys())
        filtered_kwargs = {
            key: val for key, val in raw_kwargs.items() 
            if key in valid_params
        }
        
        # Log parameter info
        logger.debug(f"Scraper {name} called with parameters: {list(filtered_kwargs.keys())}")
        
        # Execute function with timeout
        start_time = time.time()
        data = target_func(**filtered_kwargs)
        execution_time = time.time() - start_time
        
        # Validate and process results
        if not isinstance(data, list):
            logger.warning(f"Scraper {name} returned non-list data: {type(data)}")
            data = []
        
        # Add source information
        for item in data:
            if isinstance(item, dict):
                item[COL_SOURCE] = name
        
        logger.info(f"✅ Scraper {name} completed in {execution_time:.2f}s: {len(data)} items")
        return data, None
        
    except Exception as e:
        logger.error(f"❌ Scraper {name} failed: {e}", exc_info=True)
        return [], e

# ------------------ AI ANALYSIS ------------------

def sanitize_input(text: str) -> str:
    """Sanitize input text to prevent injection and encoding issues."""
    if not isinstance(text, str):
        text = str(text)
    
    # HTML escape
    text = html.escape(text)
    
    # Remove potentially problematic characters
    text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', text)
    
    # Limit length
    max_length = 10000
    if len(text) > max_length:
        text = text[:max_length] + "... [truncated]"
    
    return text

def extract_and_validate_json(content: str) -> Optional[Dict[str, Any]]:
    """Enhanced JSON extraction with multiple parsing strategies."""
    if not content or not content.strip():
        return None
    
    # Strategy 1: Direct JSON parse
    try:
        return json.loads(content.strip())
    except json.JSONDecodeError:
        pass
    
    # Strategy 2: Extract JSON object using brace counting
    brace_level = 0
    start_pos = -1
    
    for i, char in enumerate(content):
        if char == '{':
            if start_pos == -1:
                start_pos = i
            brace_level += 1
        elif char == '}':
            brace_level -= 1
            if brace_level == 0 and start_pos != -1:
                try:
                    json_str = content[start_pos:i+1]
                    return json.loads(json_str)
                except json.JSONDecodeError:
                    continue
    
    # Strategy 3: Regex fallback
    match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    
    # Strategy 4: Lenient parsing with cleanup
    try:
        # Find potential JSON
        match = re.search(r'\{.*\}', content, re.DOTALL)
        if not match:
            return None
        
        json_str = match.group(0)
        
        # Common fixes
        fixes = [
            (r',\s*}', '}'),  # Remove trailing commas in objects
            (r',\s*]', ']'),  # Remove trailing commas in arrays
            (r':\s*,', ': null,'),  # Fix empty values
            (r':\s*}', ': null}'),  # Fix empty values at end
        ]
        
        for pattern, replacement in fixes:
            json_str = re.sub(pattern, replacement, json_str)
        
        return json.loads(json_str)
        
    except (json.JSONDecodeError, AttributeError):
        logger.debug(f"All JSON parsing strategies failed for content: {content[:200]}...")
        return None

@asynccontextmanager
async def get_ai_client():
    """Async context manager for AI client with proper resource management."""
    client = None
    try:
        endpoint, api_key, deployment = get_validated_azure_config()
        client = AsyncAzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version="2024-02-01",
            timeout=AI_TIMEOUT
        )
        yield client
    finally:
        if client:
            await client.close()

async def analyze_opportunity_with_ai(
    opportunity: Dict[str, Any], 
    knowledge: str, 
    client: AsyncAzureOpenAI
) -> Optional[Dict[str, Any]]:
    """Enhanced AI analysis with better error handling and retry logic."""
    
    # Prepare and sanitize input data
    title = sanitize_input(opportunity.get('Title', ''))
    description = sanitize_input(opportunity.get('Description', ''))
    set_aside = sanitize_input(str(opportunity.get('SetAside', 'N/A')))
    naics = sanitize_input(str(opportunity.get('NAICS', 'N/A')))
    classification = sanitize_input(str(opportunity.get('Classification', 'N/A')))
    
    # Handle POC data
    poc_data = opportunity.get('POC', [])
    try:
        poc_str = json.dumps(poc_data, indent=2) if poc_data else "N/A"
        poc_str = sanitize_input(poc_str)
    except Exception:
        poc_str = "N/A"
    
    text = f"""
    Title: {title}
    Description: {description}
    Set-Aside: {set_aside}
    NAICS: {naics}
    Classification: {classification}
    POC: {poc_str}
    """.strip()
    
    if not text or len(text) < 50:
        logger.debug("Opportunity text too short for analysis")
        return None

    # Sanitize knowledge base
    safe_knowledge = sanitize_input(knowledge)

    system_prompt = """You are a government contracting opportunity analyst. Your task is to evaluate opportunities for relevance to WBI's capabilities.

Return ONLY valid JSON with these exact keys:
- "relevance_score": number between 0.0 and 1.0
- "justification": string explaining the score
- "related_experience": string describing relevant company experience
- "funding_assessment": string assessing funding potential
- "suggested_internal_lead": string suggesting who should lead

Use null for unknown values. Ensure all strings are properly escaped."""

    user_prompt = f"""WBI CAPABILITIES:
{safe_knowledge[:5000]}

OPPORTUNITY TO ANALYZE:
{text}

Provide your analysis as valid JSON only."""

    # Retry logic with exponential backoff
    for attempt in range(MAX_RETRIES):
        try:
            endpoint, api_key, deployment = get_validated_azure_config()
            
            response = await client.chat.completions.create(
                model=deployment,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3,  # Lower temperature for more consistent JSON
                max_tokens=800,
                timeout=AI_TIMEOUT
            )
            
            content = response.choices[0].message.content if response.choices and response.choices[0].message else ""
            
            if not content or not content.strip():
                logger.warning(f"Empty AI response for opportunity: {title[:50]}...")
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return None

            # Parse JSON with enhanced error handling
            result_json = extract_and_validate_json(content)
            
            if not result_json:
                logger.warning(f"Failed to parse AI response as JSON (attempt {attempt + 1}): {content[:200]}...")
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return None
            
            # Validate required fields
            required_fields = ['relevance_score', 'justification', 'related_experience', 
                             'funding_assessment', 'suggested_internal_lead']
            
            for field in required_fields:
                if field not in result_json:
                    result_json[field] = None
            
            # Validate relevance score
            try:
                score = float(result_json.get('relevance_score', 0))
                if not 0 <= score <= 1:
                    logger.warning(f"Invalid relevance score {score}, clamping to [0,1]")
                    score = max(0, min(1, score))
                result_json['relevance_score'] = score
            except (ValueError, TypeError):
                logger.warning("Invalid relevance score, defaulting to 0")
                result_json['relevance_score'] = 0.0
            
            # Add metadata
            result_json['original_opportunity'] = opportunity
            result_json['analysis_timestamp'] = datetime.now().isoformat()
            
            return result_json

        except asyncio.TimeoutError:
            logger.warning(f"AI analysis timeout for opportunity {title[:50]}... (attempt {attempt + 1})")
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(2 ** attempt)
                continue
                
        except Exception as e:
            logger.error(f"AI analysis failed for opportunity {title[:50]}... (attempt {attempt + 1}): {e}")
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(2 ** attempt)
                continue
    
    logger.error(f"AI analysis failed after {MAX_RETRIES} attempts for: {title[:50]}...")
    return None

# ------------------ PROGRESS REPORTING ------------------

class ProgressReporter:
    """Enhanced progress reporting for better UI integration."""
    
    def __init__(self, log: List[Dict[str, Any]]):
        self.log = log
        self.start_time = time.time()
    
    def add_log(self, message: str, level: str = "info", progress: Optional[float] = None):
        """Add structured log entry with optional progress."""
        timestamp = datetime.now().isoformat()
        elapsed = time.time() - self.start_time
        
        log_entry = {
            "text": message,
            "timestamp": timestamp,
            "level": level,
            "elapsed_seconds": round(elapsed, 2)
        }
        
        if progress is not None:
            log_entry["progress"] = min(100, max(0, progress))
        
        self.log.append(log_entry)
        
        # Also log to standard logger
        if level == "error":
            logger.error(message)
        elif level == "warning":
            logger.warning(message)
        else:
            logger.info(message)

# ------------------ MAIN PIPELINE ------------------

async def run_ai_analysis_pipeline(
    opportunities: List[Dict[str, Any]], 
    knowledge: str, 
    reporter: ProgressReporter
) -> List[Dict[str, Any]]:
    """Enhanced AI analysis pipeline with better batching and error handling."""
    
    if not opportunities:
        return []
    
    relevant_opportunities = []
    total_batches = (len(opportunities) + BATCH_SIZE - 1) // BATCH_SIZE
    
    async with get_ai_client() as client:
        for batch_idx in range(0, len(opportunities), BATCH_SIZE):
            batch_opps = opportunities[batch_idx:batch_idx + BATCH_SIZE]
            current_batch = batch_idx // BATCH_SIZE + 1
            
            progress = (current_batch / total_batches) * 100
            reporter.add_log(
                f"🤖 Analyzing batch {current_batch} of {total_batches} ({len(batch_opps)} opportunities)...",
                progress=progress
            )
            
            # Process batch with timeout
            try:
                tasks = [
                    analyze_opportunity_with_ai(opp, knowledge, client) 
                    for opp in batch_opps
                ]
                
                # Use asyncio.wait_for to add batch-level timeout
                batch_timeout = AI_TIMEOUT * len(batch_opps) * 2  # More generous timeout for batches
                results = await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=batch_timeout
                )
                
                # Process results
                successful_analyses = 0
                for result in results:
                    if isinstance(result, Exception):
                        logger.error(f"Batch analysis exception: {result}")
                        continue
                    
                    if result and isinstance(result, dict):
                        relevance_score = result.get('relevance_score', 0)
                        if relevance_score >= RELEVANCE_THRESHOLD:
                            # Extract original opportunity and merge with analysis
                            original_opp = result.pop('original_opportunity', {})
                            original_opp.update(result)
                            relevant_opportunities.append(original_opp)
                        successful_analyses += 1
                
                reporter.add_log(f"✅ Batch {current_batch} completed: {successful_analyses}/{len(batch_opps)} analyzed successfully")
                
                # Rate limiting
                if current_batch < total_batches:
                    await asyncio.sleep(API_DELAY)
                    
            except asyncio.TimeoutError:
                reporter.add_log(f"⚠️ Batch {current_batch} timed out", "warning")
                continue
            except Exception as e:
                reporter.add_log(f"❌ Batch {current_batch} failed: {e}", "error")
                continue
    
    return relevant_opportunities

def run_wbi_pipeline(log: List[Dict[str, Any]]) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Enhanced main pipeline with comprehensive error handling and progress reporting."""
    
    reporter = ProgressReporter(log)
    
    try:
        # Validate configuration
        validate_configuration()
    except Exception as e:
        reporter.add_log(f"❌ Configuration validation failed: {e}", "error")
        return pd.DataFrame(), pd.DataFrame()
    
    reporter.add_log("🚀 Starting WBI Pipeline...")
    
    if TESTING_MODE:
        reporter.add_log("⚠️ RUNNING IN TESTING MODE", "warning")
    
    # Clean up old database entries
    try:
        db_manager.cleanup_old_entries()
    except Exception as e:
        reporter.add_log(f"⚠️ Database cleanup failed: {e}", "warning")
    
    # Load dependencies
    try:
        knowledge = load_company_knowledge()
        if not knowledge or len(knowledge) < 100:
            reporter.add_log("⚠️ Company knowledge appears limited or empty", "warning")
    except Exception as e:
        reporter.add_log(f"❌ Failed to load company knowledge: {e}", "error")
        return pd.DataFrame(), pd.DataFrame()
    
    try:
        seen_urls = db_manager.load_previous_urls()
        reporter.add_log(f"📋 Loaded {len(seen_urls)} previously seen opportunities")
    except Exception as e:
        reporter.add_log(f"❌ Database error: {e}", "error")
        return pd.DataFrame(), pd.DataFrame()
    
    # Load scraper configuration
    try:
        config = load_scraper_config()
        if not config:
            reporter.add_log("❌ No valid scrapers configured", "error")
            return pd.DataFrame(), pd.DataFrame()
    except Exception as e:
        reporter.add_log(f"❌ Configuration loading failed: {e}", "error")
        return pd.DataFrame(), pd.DataFrame()
    
    # Handle SBIR Partnerships separately (if configured)
    sbir_partners = []
    sbir_config = next((c for c in config if c['name'] == 'SBIR Partnerships'), None)
    
    if sbir_config and sbir_config.get('enabled', True):
        reporter.add_log("🔎 Running SBIR Partnerships scraper...")
        try:
            sbir_data, sbir_error = run_scraper_task(sbir_config)
            if sbir_error:
                reporter.add_log(f"⚠️ SBIR Partnerships scraper encountered errors: {sbir_error}", "warning")
            else:
                sbir_partners = sbir_data
                reporter.add_log(f"✅ SBIR Partnerships: {len(sbir_partners)} items found")
        except Exception as e:
            reporter.add_log(f"❌ SBIR Partnerships scraper failed: {e}", "error")
    else:
        reporter.add_log("ℹ️ SBIR Partnerships scraper not enabled or configured")
    
    # Run other scrapers in parallel
    direct_scrapers = [
        c for c in config 
        if c['name'] != 'SBIR Partnerships' and c.get('enabled', True)
    ]
    
    if not direct_scrapers:
        reporter.add_log("⚠️ No direct scrapers enabled", "warning")
        all_opportunities = []
    else:
        reporter.add_log(f"🌐 Running {len(direct_scrapers)} direct scrapers in parallel...")
        
        all_opportunities = []
        failed_scrapers = []
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            # Submit all scraper tasks
            future_to_name = {
                executor.submit(run_scraper_task, scraper_config): scraper_config['name']
                for scraper_config in direct_scrapers
            }
            
            # Collect results
            completed_scrapers = 0
            for future in as_completed(future_to_name):
                scraper_name = future_to_name[future]
                completed_scrapers += 1
                
                try:
                    data, error = future.result(timeout=300)  # 5-minute timeout per scraper
                    
                    if error:
                        reporter.add_log(f"⚠️ Scraper '{scraper_name}' had errors: {error}", "warning")
                        failed_scrapers.append(scraper_name)
                    else:
                        all_opportunities.extend(data)
                        reporter.add_log(f"✅ Scraper '{scraper_name}': {len(data)} items")
                    
                    # Update progress
                    progress = (completed_scrapers / len(direct_scrapers)) * 50  # First 50% for scraping
                    reporter.add_log(f"📊 Scraping progress: {completed_scrapers}/{len(direct_scrapers)} completed", progress=progress)
                    
                except Exception as e:
                    reporter.add_log(f"❌ Scraper '{scraper_name}' failed: {e}", "error")
                    failed_scrapers.append(scraper_name)
    
    # Report scraping results
    total_found = len(all_opportunities)
    reporter.add_log(f"📊 Scraping completed: {total_found} opportunities found")
    
    if failed_scrapers:
        reporter.add_log(f"⚠️ Failed scrapers: {', '.join(failed_scrapers)}", "warning")
    
    if total_found == 0:
        reporter.add_log("ℹ️ No opportunities found - pipeline complete", "warning")
        return pd.DataFrame(), pd.DataFrame(sbir_partners)
    
    # Start AI analysis
    reporter.add_log(f"🤖 Starting AI analysis of {total_found} opportunities...")
    
    try:
        # Run AI analysis pipeline
        relevant_opportunities = asyncio.run(
            run_ai_analysis_pipeline(all_opportunities, knowledge, reporter)
        )
        
        reporter.add_log(f"🎯 AI analysis complete: {len(relevant_opportunities)} relevant opportunities (threshold: {RELEVANCE_THRESHOLD})")
        
    except Exception as e:
        reporter.add_log(f"❌ AI analysis pipeline failed: {e}", "error")
        logger.error("AI analysis pipeline error", exc_info=True)
        return pd.DataFrame(), pd.DataFrame(sbir_partners)
    
    # Create DataFrame and process results
    try:
        if relevant_opportunities:
            df_opportunities = pd.DataFrame(relevant_opportunities)
            
            # Add new/seen status
            if COL_URL in df_opportunities.columns:
                df_opportunities[COL_IS_NEW] = df_opportunities[COL_URL].apply(
                    lambda url: url not in seen_urls
                )
                
                # Save new URLs to database
                try:
                    db_manager.save_new_urls(df_opportunities)
                    new_count = df_opportunities[COL_IS_NEW].sum()
                    if new_count > 0:
                        reporter.add_log(f"💾 Saved {new_count} new opportunities to database")
                except Exception as e:
                    reporter.add_log(f"⚠️ Failed to save new URLs: {e}", "warning")
            else:
                reporter.add_log("⚠️ No URL column found - cannot track new opportunities", "warning")
                df_opportunities[COL_IS_NEW] = True
            
            # Add relevance score to standard column
            if 'relevance_score' in df_opportunities.columns:
                df_opportunities[COL_RELEVANCE] = df_opportunities['relevance_score']
            
            # Log statistics
            if COL_IS_NEW in df_opportunities.columns:
                new_opportunities = df_opportunities[df_opportunities[COL_IS_NEW]]
                reporter.add_log(f"📈 Results: {len(new_opportunities)} new, {len(df_opportunities) - len(new_opportunities)} previously seen")
            
            # Log by source
            if COL_SOURCE in df_opportunities.columns:
                source_counts = df_opportunities[COL_SOURCE].value_counts()
                source_summary = ", ".join([f"{source}: {count}" for source, count in source_counts.items()])
                reporter.add_log(f"📊 By source: {source_summary}")
        else:
            df_opportunities = pd.DataFrame()
            reporter.add_log("ℹ️ No opportunities met relevance threshold")
            
    except Exception as e:
        reporter.add_log(f"❌ Failed to process results: {e}", "error")
        logger.error("Results processing error", exc_info=True)
        return pd.DataFrame(), pd.DataFrame(sbir_partners)
    
    # Create SBIR partners DataFrame
    try:
        df_sbir_partners = pd.DataFrame(sbir_partners) if sbir_partners else pd.DataFrame()
        if not df_sbir_partners.empty:
            reporter.add_log(f"🤝 SBIR Partners: {len(df_sbir_partners)} entries")
    except Exception as e:
        reporter.add_log(f"⚠️ Failed to process SBIR partners: {e}", "warning")
        df_sbir_partners = pd.DataFrame()
    
    # Final statistics and cleanup
    pipeline_duration = time.time() - reporter.start_time
    reporter.add_log(f"🎉 Pipeline completed in {pipeline_duration:.2f} seconds", progress=100)
    
    # Log final summary
    summary_stats = {
        "total_scraped": total_found,
        "relevant_found": len(relevant_opportunities) if relevant_opportunities else 0,
        "sbir_partners": len(sbir_partners),
        "failed_scrapers": len(failed_scrapers),
        "duration_seconds": round(pipeline_duration, 2)
    }
    
    logger.info(f"Pipeline summary: {summary_stats}")
    
    return df_opportunities, df_sbir_partners

# ------------------ UTILITY FUNCTIONS ------------------

def get_pipeline_status() -> Dict[str, Any]:
    """Get current pipeline configuration and status."""
    return {
        "configuration": {
            "testing_mode": TESTING_MODE,
            "batch_size": BATCH_SIZE,
            "api_delay": API_DELAY,
            "max_retries": MAX_RETRIES,
            "max_workers": MAX_WORKERS,
            "relevance_threshold": RELEVANCE_THRESHOLD,
            "ai_timeout": AI_TIMEOUT
        },
        "environment": {
            "azure_openai_configured": all([AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY, AZURE_OPENAI_DEPLOYMENT]),
            "sam_gov_api_configured": bool(SAM_GOV_API_KEY),
            "config_file_exists": os.path.exists(CONFIG_FILE),
            "knowledge_file_exists": os.path.exists(COMPANY_KNOWLEDGE_FILE),
            "database_exists": os.path.exists(DB_FILE)
        },
        "fetch_functions_available": len(FETCH_FUNCTIONS),
        "timestamp": datetime.now().isoformat()
    }

def validate_opportunity_data(opportunity: Dict[str, Any]) -> bool:
    """Validate opportunity data structure."""
    required_fields = ['Title', 'Description']
    return all(
        field in opportunity and 
        opportunity[field] and 
        len(str(opportunity[field]).strip()) > 0
        for field in required_fields
    )

def sanitize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Sanitize DataFrame for safe processing."""
    if df.empty:
        return df
    
    # Remove any potential XSS or injection content
    string_columns = df.select_dtypes(include=['object']).columns
    
    for col in string_columns:
        df[col] = df[col].astype(str).apply(
            lambda x: html.escape(str(x)) if pd.notna(x) else x
        )
    
    return df

def create_pipeline_report(df_opportunities: pd.DataFrame, df_sbir: pd.DataFrame) -> Dict[str, Any]:
    """Create a summary report of pipeline results."""
    report = {
        "timestamp": datetime.now().isoformat(),
        "opportunities": {
            "total": len(df_opportunities),
            "new": len(df_opportunities[df_opportunities.get(COL_IS_NEW, False)]) if not df_opportunities.empty else 0,
            "average_relevance": df_opportunities.get(COL_RELEVANCE, pd.Series()).mean() if not df_opportunities.empty else 0,
            "sources": df_opportunities.get(COL_SOURCE, pd.Series()).value_counts().to_dict() if not df_opportunities.empty else {}
        },
        "sbir_partners": {
            "total": len(df_sbir)
        },
        "configuration": get_pipeline_status()["configuration"]
    }
    
    return report

# ------------------ TESTING AND DIAGNOSTICS ------------------

def run_pipeline_diagnostics() -> Dict[str, Any]:
    """Run comprehensive pipeline diagnostics."""
    diagnostics = {
        "timestamp": datetime.now().isoformat(),
        "status": "unknown",
        "checks": {}
    }
    
    try:
        # Configuration check
        diagnostics["checks"]["configuration"] = {
            "status": "checking"
        }
        
        try:
            validate_configuration()
            diagnostics["checks"]["configuration"] = {
                "status": "passed",
                "message": "All required configuration validated"
            }
        except Exception as e:
            diagnostics["checks"]["configuration"] = {
                "status": "failed",
                "error": str(e)
            }
        
        # Database check
        diagnostics["checks"]["database"] = {
            "status": "checking"
        }
        
        try:
            test_db = DatabaseManager(DB_FILE)
            test_urls = test_db.load_previous_urls()
            diagnostics["checks"]["database"] = {
                "status": "passed",
                "message": f"Database accessible, {len(test_urls)} URLs stored"
            }
        except Exception as e:
            diagnostics["checks"]["database"] = {
                "status": "failed",
                "error": str(e)
            }
        
        # Knowledge file check
        diagnostics["checks"]["knowledge"] = {
            "status": "checking"
        }
        
        try:
            knowledge = load_company_knowledge()
            diagnostics["checks"]["knowledge"] = {
                "status": "passed" if len(knowledge) > 100 else "warning",
                "message": f"Knowledge loaded: {len(knowledge)} characters"
            }
        except Exception as e:
            diagnostics["checks"]["knowledge"] = {
                "status": "failed",
                "error": str(e)
            }
        
        # Scraper configuration check
        diagnostics["checks"]["scrapers"] = {
            "status": "checking"
        }
        
        try:
            scrapers = load_scraper_config()
            diagnostics["checks"]["scrapers"] = {
                "status": "passed" if scrapers else "warning",
                "message": f"{len(scrapers)} valid scrapers configured",
                "scraper_names": [s['name'] for s in scrapers]
            }
        except Exception as e:
            diagnostics["checks"]["scrapers"] = {
                "status": "failed",
                "error": str(e)
            }
        
        # AI service check
        diagnostics["checks"]["ai_service"] = {
            "status": "checking"
        }
        
        try:
            # This is a lightweight check - we don't actually call the API
            endpoint, api_key, deployment = get_validated_azure_config()
            diagnostics["checks"]["ai_service"] = {
                "status": "configured",
                "message": "Azure OpenAI credentials configured",
                "deployment": deployment
            }
        except Exception as e:
            diagnostics["checks"]["ai_service"] = {
                "status": "failed",
                "error": str(e)
            }
        
        # Overall status
        check_statuses = [check["status"] for check in diagnostics["checks"].values()]
        
        if all(status == "passed" for status in check_statuses):
            diagnostics["status"] = "healthy"
        elif any(status == "failed" for status in check_statuses):
            diagnostics["status"] = "unhealthy"
        else:
            diagnostics["status"] = "warning"
            
    except Exception as e:
        diagnostics["status"] = "error"
        diagnostics["error"] = str(e)
        logger.error("Pipeline diagnostics failed", exc_info=True)
    
    return diagnostics

# ------------------ MODULE INITIALIZATION ------------------

def initialize_wbi_pipeline():
    """Initialize the WBI pipeline with all required components."""
    try:
        logger.info("🚀 Initializing WBI Pipeline...")
        
        # Validate configuration
        validate_configuration()
        
        # Initialize database
        db_manager._init_database()
        
        # Load and validate scrapers
        scrapers = load_scraper_config()
        logger.info(f"✅ WBI Pipeline initialized with {len(scrapers)} scrapers")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ WBI Pipeline initialization failed: {e}")
        return False

# Initialize on module import
try:
    pipeline_ready = initialize_wbi_pipeline()
    if not pipeline_ready:
        logger.warning("WBI Pipeline initialization incomplete - some features may not work")
except Exception as e:
    logger.error(f"WBI Pipeline startup error: {e}")
    pipeline_ready = False

# Export main functions and status
__all__ = [
    'run_wbi_pipeline',
    'get_pipeline_status', 
    'run_pipeline_diagnostics',
    'create_pipeline_report',
    'validate_opportunity_data',
    'pipeline_ready'
]
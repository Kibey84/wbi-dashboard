import os
import pandas as pd
from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify, request, send_from_directory, send_file
import io
import json
import logging
import threading
import uuid
import re
from xlsxwriter.workbook import Workbook
from typing import cast, Optional, Dict, Any, List
import time
from fpdf import FPDF
import asyncio

from tools import org_chart_parser
from tools import wbiops

from azure.ai.inference import ChatCompletionsClient
from azure.ai.inference.models import SystemMessage, UserMessage
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError, ServiceRequestError

from azure.storage.blob import BlobServiceClient
from openai import AsyncAzureOpenAI

# --- Configuration Constants ---
MAX_RETRY_ATTEMPTS = 3
BASE_RETRY_DELAY = 1.2
MAX_RETRY_DELAY = 5.0
REQUEST_TIMEOUT = 45
JOB_CLEANUP_INTERVAL = 3600  # 1 hour
JOB_RETENTION_TIME = 7200    # 2 hours

# --- Configuration for AI Models ---
# GPT-4.x (planning / validation / final formatting)
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT")

# DeepSeek (main BoE estimator)
DEEPSEEK_ENDPOINT = os.getenv("DEEPSEEK_AZURE_ENDPOINT") or os.getenv("DEEPSEEK_ENDPOINT")
DEEPSEEK_KEY = os.getenv("DEEPSEEK_AZURE_KEY") or os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_DEPLOYMENT = os.getenv("DEEPSEEK_DEPLOYMENT", "WBI-Dash-DeepSeek")

def validate_configuration() -> None:
    """Validate required environment variables at startup."""
    required_vars = [
        ("AZURE_OPENAI_ENDPOINT", AZURE_OPENAI_ENDPOINT),
        ("AZURE_OPENAI_KEY", AZURE_OPENAI_KEY), 
        ("AZURE_OPENAI_DEPLOYMENT", AZURE_OPENAI_DEPLOYMENT),
        ("DEEPSEEK_ENDPOINT", DEEPSEEK_ENDPOINT),
        ("DEEPSEEK_KEY", DEEPSEEK_KEY)
    ]
    
    missing = [name for name, value in required_vars if not value]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
    
    logger.info("✅ Configuration validated successfully")

def _ds_endpoint_base() -> str:
    """Accept either …/models or root; normalize to …/models"""
    base = (DEEPSEEK_ENDPOINT or "").rstrip("/")
    return base if base.endswith("/models") else f"{base}/models"

def _get_deepseek_client() -> ChatCompletionsClient:
    """Get DeepSeek client with validated configuration."""
    ep = _ds_endpoint_base()
    key = (DEEPSEEK_KEY or "").strip()
    if not ep or not key:
        raise RuntimeError("DeepSeek config missing (endpoint/key).")
    return ChatCompletionsClient(
        endpoint=ep,
        credential=AzureKeyCredential(key),
        api_version="2024-05-01-preview",
    )

def deepseek_complete(messages, max_tokens=2048, temperature=0.2, request_timeout=REQUEST_TIMEOUT):
    """Enhanced DeepSeek completion with exponential backoff."""
    client = _get_deepseek_client()
    
    for attempt in range(MAX_RETRY_ATTEMPTS):
        try:
            return client.complete(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                model=(DEEPSEEK_DEPLOYMENT or "DeepSeek-R1-0528"),
                stream=False,
                timeout=request_timeout,
            )
        except (HttpResponseError, ServiceRequestError) as e:
            code = getattr(e, "status_code", None)
            is_retryable = code in (429, 500, 502, 503, 504) or isinstance(e, ServiceRequestError)
            
            if attempt < MAX_RETRY_ATTEMPTS - 1 and is_retryable:
                delay = min(BASE_RETRY_DELAY * (2 ** attempt), MAX_RETRY_DELAY)
                logger.warning(f"DeepSeek request failed (attempt {attempt + 1}), retrying in {delay}s: {e}")
                time.sleep(delay)
                continue
            
            logger.error(f"DeepSeek request failed after {attempt + 1} attempts: {e}")
            raise
    
    raise RuntimeError("DeepSeek request failed after all retry attempts")

# Initialize Flask app
basedir = os.path.abspath(os.path.dirname(__file__))
app = Flask(
    __name__,
    template_folder=os.path.join(basedir, "templates"),
    static_folder=os.path.join(basedir, "static"),
)

REPORTS_DIR = os.getenv("REPORTS_DIR", "/home/data/generated_reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

BLOB_CONTAINER_NAME = "data"
PROJECT_DATA_FILE = "MockReportToolFile.xlsx"
UPDATES_FILE = "updates.csv"

# Configure logging
if not logging.getLogger().hasHandlers():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

logger = logging.getLogger("wbi")

# Job tracking with timestamps
pipeline_jobs: Dict[str, Dict[str, Any]] = {}
estimate_jobs: Dict[str, Dict[str, Any]] = {}

def cleanup_old_jobs() -> None:
    """Remove old completed jobs to prevent memory leaks."""
    current_time = time.time()
    cutoff_time = current_time - JOB_RETENTION_TIME
    
    # Clean pipeline jobs
    expired_pipeline = [
        job_id for job_id, job_data in pipeline_jobs.items()
        if job_data.get("created_at", current_time) < cutoff_time
    ]
    for job_id in expired_pipeline:
        del pipeline_jobs[job_id]
    
    # Clean estimate jobs  
    expired_estimate = [
        job_id for job_id, job_data in estimate_jobs.items()
        if job_data.get("created_at", current_time) < cutoff_time
    ]
    for job_id in expired_estimate:
        del estimate_jobs[job_id]
    
    if expired_pipeline or expired_estimate:
        logger.info(f"Cleaned up {len(expired_pipeline)} pipeline jobs and {len(expired_estimate)} estimate jobs")

def start_cleanup_timer() -> None:
    """Start periodic job cleanup."""
    def cleanup_loop():
        while True:
            try:
                cleanup_old_jobs()
                time.sleep(JOB_CLEANUP_INTERVAL)
            except Exception as e:
                logger.error(f"Job cleanup failed: {e}")
                time.sleep(JOB_CLEANUP_INTERVAL)
    
    cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True)
    cleanup_thread.start()
    logger.info("Started job cleanup timer")

# ======================================================================
# AI ESTIMATION FUNCTIONS
# ======================================================================

def deepseek_emit_estimate(new_request: str, case_history: str) -> Dict[str, Any]:
    """
    Enhanced multi-step estimation with better error handling and validation.
    """
    try:
        # Step 1: High-level planning
        logger.info("Step 1: Generating high-level plan")
        plan_resp = deepseek_complete([
            SystemMessage(content="You are a strategic planner. Return a concise bullet list of BoE sections."),
            UserMessage(content=f"Create a high-level plan for a BoE based on this request:\n\n{new_request}")
        ], max_tokens=500, request_timeout=40)
        plan = (plan_resp.choices[0].message.content or "").strip()
        
        if not plan:
            raise ValueError("Planning step returned empty response")

        # Step 2: Detailed estimation
        logger.info("Step 2: Generating detailed estimation")
        est_resp = deepseek_complete([
            SystemMessage(content=(
                "You are a senior cost estimator. Produce detailed cost breakdown with specific tasks, "
                "labor hours by role, materials, travel, and subcontractor costs. Be specific and realistic."
            )),
            UserMessage(content=(
                f"**Case History:**\n{case_history}\n\n"
                f"**High-Level Plan:**\n{plan}\n\n"
                f"**New Request:**\n{new_request}\n\n"
                f"**Your Task:** Generate detailed cost estimation data."
            ))
        ], max_tokens=2200, request_timeout=60)
        detailed = (est_resp.choices[0].message.content or "").strip()
        
        if not detailed:
            raise ValueError("Estimation step returned empty response")

        # Step 3: JSON formatting with validation
        logger.info("Step 3: Formatting as structured JSON")
        final_resp = deepseek_complete([
            SystemMessage(content=(
                "You are a strict JSON formatter. Return ONE valid, complete JSON object only. "
                "Required structure:\n"
                "{\n"
                '  "project_title": "string",\n'
                '  "start_date": "YYYY-MM-DD",\n'
                '  "pop": "string (e.g., 12 months)",\n'
                '  "work_plan": [{"task": "string", "hours": {"PM": number, "SE": number}}],\n'
                '  "materials_and_tools": [{"part_number": "string", "description": "string", "vendor": "string", "quantity": number, "unit_cost": number}],\n'
                '  "travel": [{"purpose": "string", "trips": number, "travelers": number, "days": number, "airfare": number, "lodging": number, "per_diem": number}],\n'
                '  "subcontracts": [{"subcontractor": "string", "description": "string", "cost": number}]\n'
                "}\n"
                "Use empty arrays [] for missing sections, 0 for unknown numbers, empty strings for unknown text."
            )),
            UserMessage(content=(
                f"**Original Request:**\n{new_request}\n\n"
                f"**Detailed Estimation Data:**\n{detailed}\n\n"
                f"**Task:** Convert to the exact JSON structure specified."
            ))
        ], max_tokens=2200, request_timeout=60)

        final_json_str = (final_resp.choices[0].message.content or "").strip()
        
        if not final_json_str:
            raise ValueError("JSON formatting step returned empty response")

        # Enhanced JSON parsing with multiple fallback strategies
        data = _extract_and_validate_json(final_json_str)
        if not data:
            logger.error("Failed to extract valid JSON. Raw response (first 500 chars): %s", final_json_str[:500])
            raise ValueError("Model returned no valid JSON after all parsing attempts.")
        
        # Validate required fields
        required_fields = ["project_title", "work_plan", "materials_and_tools", "travel", "subcontracts"]
        for field in required_fields:
            if field not in data:
                logger.warning(f"Missing required field '{field}', adding default value")
                data[field] = [] if field != "project_title" else "Untitled Project"
        
        logger.info("✅ Successfully generated BoE estimate")
        return data
        
    except Exception as e:
        logger.error(f"DeepSeek estimation failed: {e}", exc_info=True)
        raise

def _extract_and_validate_json(text: str) -> Optional[Dict[str, Any]]:
    """Enhanced JSON extraction with multiple parsing strategies."""
    if not text:
        return None
    
    # Strategy 1: Direct JSON parse (if response is clean)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    
    # Strategy 2: Extract JSON object from mixed content
    json_obj = _extract_json_from_response(text)
    if json_obj:
        return json_obj
    
    # Strategy 3: Lenient parsing with cleanup
    return _try_lenient_json(text)

def _extract_json_from_response(text: str) -> Optional[Dict]:
    """Extract a JSON object from a string (handles models that add prose)."""
    # Find JSON object boundaries
    brace_level = 0
    start_pos = -1
    
    for i, char in enumerate(text):
        if char == '{':
            if start_pos == -1:
                start_pos = i
            brace_level += 1
        elif char == '}':
            brace_level -= 1
            if brace_level == 0 and start_pos != -1:
                try:
                    json_str = text[start_pos:i+1]
                    return json.loads(json_str)
                except json.JSONDecodeError:
                    continue
    
    # Fallback to regex if brace counting fails
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            logger.debug("Regex-extracted JSON failed to parse")
    
    return None

def _try_lenient_json(text: str) -> Optional[Dict]:
    """Attempt to fix common JSON formatting issues."""
    if not text:
        return None
    
    # Extract potential JSON
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if not match:
        return None
    
    json_str = match.group(0)
    
    # Common fixes
    fixes = [
        (r'\{\s*\.\.\.\s*\}', '{}'),  # Replace {...} with {}
        (r'\[\s*\.\.\.\s*\]', '[]'),  # Replace [...] with []
        (r'\[calculated value\]', '0'),  # Replace placeholders
        (r'\bNaN\b', '0'),  # Replace NaN
        (r'(?m)^\s*//.*$', ''),  # Remove JS-style comments
        (r',\s*}', '}'),  # Fix trailing commas in objects
        (r',\s*]', ']'),  # Fix trailing commas in arrays
    ]
    
    for pattern, replacement in fixes:
        json_str = re.sub(pattern, replacement, json_str)
    
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.debug(f"Lenient JSON parsing failed: {e}")
        return None

# ======================================================================
# BACKGROUND JOB FUNCTIONS
# ======================================================================

def run_pipeline_logic(job_id: str) -> None:
    """Enhanced pipeline with better error handling and logging."""
    current_time = time.time()
    pipeline_jobs[job_id] = {
        "status": "running",
        "phase": "initializing",
        "log": [{"text": "✅ Pipeline Started", "timestamp": datetime.now().isoformat()}],
        "opps_report_filename": None,
        "match_report_filename": None,
        "created_at": current_time,
    }
    log = pipeline_jobs[job_id]["log"]

    def add_log(message: str, level: str = "info"):
        log_entry = {"text": message, "timestamp": datetime.now().isoformat(), "level": level}
        log.append(log_entry)
        if level == "error":
            logger.error(message)
        else:
            logger.info(message)

    try:
        pipeline_jobs[job_id]["phase"] = "scraping opportunities"
        add_log("🔍 Starting opportunity scraping...")
        
        pipeline_result = wbiops.run_wbi_pipeline(log)

        if pipeline_result:
            opps_df, matchmaking_df = pipeline_result
            add_log(f"📊 Retrieved {len(opps_df)} opportunities, {len(matchmaking_df)} matches")
        else:
            add_log("❌ Pipeline returned no data", "error")
            opps_df, matchmaking_df = pd.DataFrame(), pd.DataFrame()

        pipeline_jobs[job_id]["phase"] = "generating reports"
        
        if not opps_df.empty:
            timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            opps_filename = f"Opportunity_Report_{timestamp}.xlsx"
            opps_path = os.path.join(REPORTS_DIR, opps_filename)
            
            opps_df.to_excel(opps_path, index=False)
            pipeline_jobs[job_id]["opps_report_filename"] = opps_filename
            add_log(f"📊 Primary Report Generated: {opps_filename}")

        if not matchmaking_df.empty:
            timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            match_filename = f"Strategic_Matchmaking_Report_{timestamp}.xlsx"
            match_path = os.path.join(REPORTS_DIR, match_filename)
            
            matchmaking_df.to_excel(match_path, index=False)
            pipeline_jobs[job_id]["match_report_filename"] = match_filename
            add_log(f"🤝 Matchmaking Report Generated: {match_filename}")

        pipeline_jobs[job_id]["phase"] = "completed"
        add_log("🎉 Pipeline completed successfully!")
        pipeline_jobs[job_id]["status"] = "completed"

    except Exception as e:
        error_msg = f"Critical Error: {str(e)}"
        add_log(f"❌ {error_msg}", "error")
        pipeline_jobs[job_id]["status"] = "failed"
        pipeline_jobs[job_id]["phase"] = "error"
        logger.error(f"Pipeline job {job_id} failed: {e}", exc_info=True)

def _run_boe_job(job_id: str, new_request: str, case_history: str) -> None:
    """Enhanced BoE job with better tracking and error handling."""
    current_time = time.time()
    estimate_jobs[job_id] = {
        "status": "running",
        "log": [],
        "result": None,
        "error": None,
        "created_at": current_time,
    }
    log = estimate_jobs[job_id]["log"]
    
    def add_log(message: str):
        log_entry = {"text": message, "timestamp": datetime.now().isoformat()}
        log.append(log_entry)
        logger.info(f"BoE Job {job_id}: {message}")
    
    try:
        add_log("🤖 Starting DeepSeek estimation process...")
        add_log("📋 Step 1: Planning phase")
        add_log("💰 Step 2: Cost estimation phase")  
        add_log("📄 Step 3: JSON formatting phase")
        
        data = deepseek_emit_estimate(new_request, case_history)
        
        estimate_jobs[job_id]["result"] = data
        estimate_jobs[job_id]["status"] = "completed"
        add_log("✅ BoE estimation completed successfully")
        
    except Exception as e:
        error_msg = str(e)
        estimate_jobs[job_id]["status"] = "failed"
        estimate_jobs[job_id]["error"] = error_msg
        add_log(f"❌ Estimation failed: {error_msg}")
        logger.error("BoE job %s failed: %s", job_id, e, exc_info=True)

# ======================================================================
# UTILITY FUNCTIONS (Enhanced)
# ======================================================================

def get_unique_pms() -> List[str]:
    """Get unique project managers with error handling."""
    try:
        df, error = load_project_data()
        if error or df.empty:
            logger.warning(f"Failed to load project data for PMs: {error}")
            return []
        return sorted(df["pm"].dropna().unique().tolist())
    except Exception as e:
        logger.error(f"Error getting unique PMs: {e}")
        return []

def load_project_data():
    """Enhanced project data loading with better error messages."""
    connect_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if not connect_str:
        error_msg = "AZURE_STORAGE_CONNECTION_STRING environment variable not configured"
        logger.error(error_msg)
        return pd.DataFrame(), error_msg
    
    try:
        blob_service_client = BlobServiceClient.from_connection_string(connect_str)
        blob_client = blob_service_client.get_blob_client(BLOB_CONTAINER_NAME, PROJECT_DATA_FILE)
        
        if not blob_client.exists():
            error_msg = f"Project data file '{PROJECT_DATA_FILE}' not found in container '{BLOB_CONTAINER_NAME}'"
            logger.error(error_msg)
            return pd.DataFrame(), error_msg
        
        with io.BytesIO() as stream:
            download_stream = blob_client.download_blob()
            download_stream.readinto(stream)
            stream.seek(0)
            df = pd.read_excel(stream)
        
        # Validate and transform data
        column_mapping = {
            "project_id": "projectName",
            "project_pia": "pi", 
            "project_owner": "pm",
            "project_date_started": "startDate",
            "project_date_completed": "endDate",
            "Status": "status",
            "project_description": "description",
        }
        
        missing_cols = [col for col in column_mapping if col not in df.columns]
        if missing_cols:
            error_msg = f"Missing required columns in project data: {missing_cols}"
            logger.error(error_msg)
            return pd.DataFrame(), error_msg
        
        # Transform data
        df_filtered = df[list(column_mapping)].rename(columns=column_mapping)
        df_filtered["startDate"] = pd.to_datetime(df_filtered["startDate"], errors="coerce").dt.strftime("%Y-%m-%d")
        df_filtered["endDate"] = pd.to_datetime(df_filtered["endDate"], errors="coerce").dt.strftime("%Y-%m-%d")
        df_filtered.fillna("", inplace=True)
        
        logger.info(f"Successfully loaded {len(df_filtered)} project records")
        return df_filtered, None
        
    except Exception as e:
        error_msg = f"Failed to load project data: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return pd.DataFrame(), error_msg

# ======================================================================
# ASYNC AI HELPERS (Enhanced)
# ======================================================================

async def _call_ai_agent(
    client: AsyncAzureOpenAI,
    model_deployment: str,
    system_prompt: str,
    user_prompt: str,
    is_json: bool = False,
    max_retries: int = 2,
) -> str:
    """Enhanced AI agent call with retry logic."""
    base_kwargs = dict(
        model=model_deployment,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,
        max_tokens=4096,
    )
    
    last_exception = None
    for attempt in range(max_retries + 1):
        try:
            if is_json and attempt == 0:  # Try JSON mode first
                response = await client.chat.completions.create(
                    **base_kwargs,  # type: ignore[arg-type]
                    response_format={"type": "json_object"},  # type: ignore[arg-type]
                )
            else:
                response = await client.chat.completions.create(**base_kwargs)  # type: ignore[arg-type]

            content = (response.choices[0].message.content or "").strip()
            if not content:
                raise ValueError("AI returned an empty response")
                
            return content
            
        except Exception as e:
            last_exception = e
            if attempt < max_retries:
                logger.warning(f"AI call failed (attempt {attempt + 1}), retrying: {e}")
                await asyncio.sleep(1.0 * (attempt + 1))  # Exponential backoff
                continue
    
    # FIX: Explicitly raise an error if all retries fail.
    raise RuntimeError("AI call failed after all retries.") from last_exception

async def get_improved_ai_summary(description: str, update: str) -> str:
    """Enhanced AI summary with better error handling."""
    endpoint_str: str = (AZURE_OPENAI_ENDPOINT or "").strip()
    key_str: str = (AZURE_OPENAI_KEY or "").strip()
    deployment_str: str = (AZURE_OPENAI_DEPLOYMENT or "").strip()

    if not all([endpoint_str, key_str, deployment_str]):
        error_msg = "Azure OpenAI configuration incomplete"
        logger.error(error_msg)
        return f"Error: {error_msg}"
    
    if not update.strip():
        return "No update provided"

    system_prompt = (
        "You are an expert Project Manager who transforms brief updates into clear, "
        "professional status summaries for executive audiences. Focus on progress, "
        "challenges, and next steps. Keep responses concise but informative."
    )
    user_prompt = (
        f"**Project Description:**\n{description}\n\n"
        f"**Brief Update:**\n{update}\n\n"
        f"**Task:** Rewrite as a professional status summary:"
    )

    client: Optional[AsyncAzureOpenAI] = None
    try:
        client = AsyncAzureOpenAI(
            azure_endpoint=endpoint_str,
            api_key=key_str,
            api_version="2024-02-01",
        )
        result = await _call_ai_agent(client, deployment_str, system_prompt, user_prompt, is_json=False)
        logger.info("Successfully generated AI summary")
        return result
        
    except Exception as e:
        error_msg = f"AI summary generation failed: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return error_msg
        
    finally:
        if client:
            await client.close()

# ======================================================================
# FLASK ROUTES (Enhanced)
# ======================================================================

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/status")
def status():
    return jsonify({
        "status": "running",
        "timestamp": datetime.now().isoformat(),
        "active_pipeline_jobs": len(pipeline_jobs),
        "active_estimate_jobs": len(estimate_jobs)
    })

@app.route("/api/run-pipeline", methods=["POST"])
def api_run_pipeline():
    try:
        job_id = str(uuid.uuid4())
        thread = threading.Thread(target=run_pipeline_logic, args=(job_id,), daemon=True)
        thread.start()
        logger.info(f"Started pipeline job {job_id}")
        return jsonify({"job_id": job_id, "status": "queued"})
    except Exception as e:
        logger.error(f"Failed to start pipeline: {e}")
        return jsonify({"error": "Failed to start pipeline"}), 500

@app.route("/api/pipeline-status/<job_id>", methods=["GET"])
def get_pipeline_status(job_id):
    job = pipeline_jobs.get(job_id)
    if job is None:
        return jsonify({"status": "not_found", "error": "Job not found"}), 404
    return jsonify(job)

@app.route("/api/parse-org-chart", methods=["POST"])
def api_parse_org_chart():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    
    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400
    
    try:
        logger.info(f"Processing org chart file: {file.filename}")
        output_filename = org_chart_parser.process_uploaded_pdf(file, REPORTS_DIR)
        
        if output_filename:
            logger.info(f"Org chart processed successfully: {output_filename}")
            return jsonify({"success": True, "filename": output_filename})
        else:
            logger.error("Org chart processing returned no output")
            return jsonify({"error": "Processing failed - no output generated"}), 500
            
    except Exception as e:
        logger.error(f"Org chart parsing failed: {e}", exc_info=True)
        return jsonify({"error": f"Processing failed: {str(e)}"}), 500

@app.route("/api/pms")
def api_get_pms():
    try:
        pms = get_unique_pms()
        return jsonify(pms)
    except Exception as e:
        logger.error(f"Failed to get PMs: {e}")
        return jsonify({"error": "Failed to retrieve project managers"}), 500

@app.route("/api/projects")
def api_get_projects():
    pm_name = request.args.get("pm")
    if not pm_name:
        return jsonify([])
    
    try:
        projects_df, error = load_project_data()
        if error:
            return jsonify({"error": error}), 500
        
        filtered_projects = projects_df[projects_df["pm"] == pm_name]
        return jsonify(filtered_projects.to_dict(orient="records"))
        
    except Exception as e:
        logger.error(f"Failed to get projects: {e}")
        return jsonify({"error": "Failed to retrieve projects"}), 500

@app.route("/api/get_update")
def api_get_update():
    project_name = request.args.get("projectName")
    month = request.args.get("month")
    year_str = request.args.get("year")
    
    if not all([project_name, month, year_str]):
        return jsonify({"error": "Missing required parameters"}), 400
    
    try:
        year = int(year_str or 0)
        connect_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
        if not connect_str:
            return jsonify({"error": "Storage not configured"}), 500

        blob_service_client = BlobServiceClient.from_connection_string(connect_str)
        blob_client = blob_service_client.get_blob_client(BLOB_CONTAINER_NAME, UPDATES_FILE)
        
        if not blob_client.exists():
            return jsonify({})

        with io.BytesIO() as stream:
            blob_client.download_blob().readinto(stream)
            stream.seek(0)
            updates_df = pd.read_csv(stream)

        updates_df["year"] = pd.to_numeric(updates_df["year"], errors="coerce").fillna(0).astype(int)
        
        update = updates_df[
            (updates_df["projectName"] == project_name)
            & (updates_df["month"] == month)
            & (updates_df["year"] == year)
        ]
        
        result = update.iloc[0].to_dict() if not update.empty else {}
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error retrieving update: {e}")
        return jsonify({"error": "Failed to retrieve update"}), 500

@app.route("/api/update_project", methods=["POST"])
async def api_update_project():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Invalid request data"}), 400

        # Validate required fields
        required_fields = ["projectName", "month", "year", "managerUpdate"]
        missing_fields = [field for field in required_fields if not data.get(field)]
        if missing_fields:
            return jsonify({
                "success": False, 
                "error": f"Missing required fields: {', '.join(missing_fields)}"
            }), 400

        # Generate AI summary
        description = data.get("description", "")
        manager_update = data["managerUpdate"]
        
        logger.info(f"Generating AI summary for project: {data['projectName']}")
        ai_summary = await get_improved_ai_summary(description, manager_update)

        # Prepare new entry
        year_val = int(data["year"])
        new_entry = pd.DataFrame([{
            "projectName": data["projectName"],
            "month": data["month"],
            "year": year_val,
            "managerUpdate": manager_update,
            "aiSummary": ai_summary,
        }])

        # Save to Azure Blob Storage
        connect_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
        if not connect_str:
            return jsonify({"success": False, "error": "Storage not configured"}), 500

        blob_service_client = BlobServiceClient.from_connection_string(connect_str)
        blob_client = blob_service_client.get_blob_client(BLOB_CONTAINER_NAME, UPDATES_FILE)

        # Load existing data or create new
        if blob_client.exists():
            with io.BytesIO() as stream:
                blob_client.download_blob().readinto(stream)
                stream.seek(0)
                updates_df = pd.read_csv(stream)
            
            updates_df["year"] = pd.to_numeric(updates_df["year"], errors="coerce").fillna(0).astype(int)
            
            # Remove existing entry for same project/month/year
            updates_df = updates_df[
                ~(
                    (updates_df["projectName"] == data["projectName"])
                    & (updates_df["month"] == data["month"])
                    & (updates_df["year"] == year_val)
                )
            ]
            final_df = pd.concat([updates_df, new_entry], ignore_index=True)
        else:
            final_df = new_entry

        # Save updated data
        csv_data = final_df.to_csv(index=False)
        blob_client.upload_blob(csv_data, overwrite=True)
        
        logger.info(f"Successfully updated project: {data['projectName']}")
        return jsonify({"success": True, "aiSummary": ai_summary})

    except Exception as e:
        logger.error(f"Update save error: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Failed to save update"}), 500

@app.route("/api/rates", methods=["GET"])
def get_rates():
    try:
        rates_path = os.path.join(basedir, "tools", "rates.json")
        if not os.path.exists(rates_path):
            logger.error(f"Rates file not found: {rates_path}")
            return jsonify({"error": "Rates configuration file not found"}), 404
            
        with open(rates_path, "r", encoding="utf-8") as f:
            rates_data = json.load(f)
        
        return jsonify(rates_data)
        
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in rates.json: {e}")
        return jsonify({"error": "Invalid rates configuration format"}), 500
    except Exception as e:
        logger.error(f"Error loading rates.json: {e}")
        return jsonify({"error": "Could not load labor rates"}), 500

@app.route("/api/estimate", methods=["POST"])
def api_estimate_start():
    try:
        data = request.get_json() or {}
        new_request = data.get("new_request", "").strip()
        case_history = data.get("case_history", "").strip()
        
        if not new_request:
            return jsonify({"error": "Request description is required"}), 400
        
        if len(new_request) < 10:
            return jsonify({"error": "Request description too short (minimum 10 characters)"}), 400

        job_id = str(uuid.uuid4())
        thread = threading.Thread(
            target=_run_boe_job, 
            args=(job_id, new_request, case_history), 
            daemon=True
        )
        thread.start()
        
        logger.info(f"Started BoE estimation job {job_id}")
        return jsonify({"job_id": job_id, "status": "queued"}), 202

    except Exception as e:
        logger.error(f"Failed to start estimation: {e}")
        return jsonify({"error": "Failed to start estimation"}), 500

@app.route("/api/estimate/<job_id>", methods=["GET"])
def api_estimate_status(job_id: str):
    job = estimate_jobs.get(job_id)
    if not job:
        return jsonify({"status": "not_found", "error": "Job not found"}), 404
    
    # Return job status with all details
    return jsonify(job)

@app.route("/api/selftest/deepseek")
def selftest_deepseek():
    try:
        logger.info("Running DeepSeek self-test")
        resp = deepseek_complete([
            SystemMessage(content="You are a test assistant. Respond with valid JSON only."),
            UserMessage(content='Return this exact JSON: {"test": "passed", "model": "deepseek"}')
        ], max_tokens=50, temperature=0)
        
        text = (resp.choices[0].message.content or "").strip()
        logger.info(f"DeepSeek self-test response: {text}")
        
        # Try to parse the response as JSON
        try:
            parsed = json.loads(text)
            return jsonify({"ok": True, "parsed": parsed, "raw": text})
        except json.JSONDecodeError:
            return jsonify({"ok": True, "parsed": None, "raw": text, "warning": "Response not JSON"})
            
    except Exception as e:
        logger.error(f"DeepSeek self-test failed: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/selftest/aoai")
def selftest_aoai():
    import asyncio
    
    async def test_aoai():
        client = None
        try:
            endpoint = (AZURE_OPENAI_ENDPOINT or "").strip()
            key = (AZURE_OPENAI_KEY or "").strip()
            deployment = (AZURE_OPENAI_DEPLOYMENT or "").strip()
            
            if not all([endpoint, key, deployment]):
                raise ValueError("Azure OpenAI configuration incomplete")
            
            client = AsyncAzureOpenAI(
                azure_endpoint=endpoint,
                api_key=key,
                api_version="2024-02-01",
            )
            
            resp = await client.chat.completions.create(
                model=deployment,
                messages=[
                    {"role": "system", "content": "You are a test assistant. Output valid JSON only."},
                    {"role": "user", "content": 'Return: {"test": "passed", "model": "gpt4"}'}
                ],
                response_format={"type": "json_object"},
                max_tokens=50,
                temperature=0,
            )
            
            content = (resp.choices[0].message.content or "").strip()
            try:
                parsed = json.loads(content)
                return {"ok": True, "parsed": parsed, "raw": content}
            except json.JSONDecodeError:
                return {"ok": True, "parsed": None, "raw": content, "warning": "Response not JSON"}
                
        except Exception as e:
            logger.error(f"Azure OpenAI self-test failed: {e}")
            return {"ok": False, "error": str(e)}
        finally:
            if client:
                await client.close()
    
    try:
        logger.info("Running Azure OpenAI self-test")
        result = asyncio.run(test_aoai())
        return jsonify(result)
    except Exception as e:
        logger.error(f"Azure OpenAI self-test error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/generate-boe-excel", methods=["POST"])
def generate_boe_excel():
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No data provided"}), 400
            
        project_data = data.get("projectData")
        totals = data.get("totals")
        
        if not project_data:
            return jsonify({"error": "Project data is required"}), 400
        if not totals:
            return jsonify({"error": "Totals data is required"}), 400
        
        logger.info(f"Generating Excel BoE for project: {project_data.get('project_title', 'Unknown')}")
        
        excel_stream = create_formatted_boe_excel(project_data, totals)
        project_title = project_data.get("project_title", "BoE_Report").replace(" ", "_")
        project_title = re.sub(r'[^\w\-_\.]', '', project_title)  # Sanitize filename
        filename = f"BoE_{project_title}_Full.xlsx"
        
        return send_file(
            excel_stream,
            as_attachment=True,
            download_name=filename,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        
    except Exception as e:
        logger.error(f"Failed to generate BoE Excel file: {e}", exc_info=True)
        return jsonify({"error": f"Failed to generate Excel file: {str(e)}"}), 500

@app.route("/api/generate-boe-pdf", methods=["POST"])
def generate_boe_pdf():
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No data provided"}), 400
            
        project_data = data.get("projectData")
        totals = data.get("totals")
        
        if not project_data:
            return jsonify({"error": "Project data is required"}), 400
        if not totals:
            return jsonify({"error": "Totals data is required"}), 400
        
        logger.info(f"Generating PDF BoE for project: {project_data.get('project_title', 'Unknown')}")
        
        pdf_stream = create_boe_pdf(project_data, totals)
        project_title = project_data.get("project_title", "BoE_Report").replace(" ", "_")
        project_title = re.sub(r'[^\w\-_\.]', '', project_title)  # Sanitize filename
        filename = f"BoE_{project_title}_Customer.pdf"
        
        return send_file(
            pdf_stream, 
            as_attachment=True, 
            download_name=filename, 
            mimetype="application/pdf"
        )
        
    except Exception as e:
        logger.error(f"Failed to generate BoE PDF file: {e}", exc_info=True)
        return jsonify({"error": f"Failed to generate PDF file: {str(e)}"}), 500

# ======================================================================
# REPORT GENERATION FUNCTIONS (Enhanced)
# ======================================================================

def create_formatted_boe_excel(project_data: Dict[str, Any], totals: Dict[str, Any]) -> io.BytesIO:
    """Enhanced Excel generation with better formatting and error handling."""
    output_stream = io.BytesIO()
    
    try:
        with pd.ExcelWriter(output_stream, engine="xlsxwriter") as writer:
            workbook = cast(Workbook, writer.book)
            
            # Define formats
            title_format = workbook.add_format({
                "bold": True, 
                "font_size": 16, 
                "align": "left",
                "bg_color": "#4472C4",
                "font_color": "white"
            })
            header_format = workbook.add_format({
                "bold": True,
                "bg_color": "#D9E2F3", 
                "border": 1
            })
            currency_format = workbook.add_format({
                "num_format": "$#,##0.00",
                "border": 1
            })
            total_format = workbook.add_format({
                "num_format": "$#,##0.00", 
                "bold": True, 
                "bg_color": "#FFE699",
                "border": 2
            })
            
            # Cost Summary Sheet
            summary_data = [
                {"Cost Element": "Direct Labor", "Amount": totals.get("laborCost", 0)},
                {"Cost Element": "Materials & Tools", "Amount": totals.get("materialsCost", 0)},
                {"Cost Element": "Travel", "Amount": totals.get("travelCost", 0)},
                {"Cost Element": "Subcontracts", "Amount": totals.get("subcontractCost", 0)},
                {"Cost Element": "Total Direct Costs", "Amount": totals.get("totalDirectCosts", 0)},
                {"Cost Element": "Overhead", "Amount": totals.get("overheadAmount", 0)},
                {"Cost Element": "Subtotal", "Amount": totals.get("subtotal", 0)},
                {"Cost Element": "G&A", "Amount": totals.get("gnaAmount", 0)},
                {"Cost Element": "Total Cost", "Amount": totals.get("totalCost", 0)},
                {"Cost Element": "Fee", "Amount": totals.get("feeAmount", 0)},
                {"Cost Element": "Total Proposed Price", "Amount": totals.get("totalPrice", 0)},
            ]
            
            summary_df = pd.DataFrame(summary_data)
            summary_df.to_excel(writer, sheet_name="Cost Summary", index=False, startrow=4)
            
            summary_ws = writer.sheets["Cost Summary"]
            
            # Add title and project info
            project_title = project_data.get("project_title", "Basis of Estimate")
            summary_ws.merge_range("A1:B1", f"BoE Summary: {project_title}", title_format)
            summary_ws.write("A2", f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
            
            # Format columns and headers
            summary_ws.set_column("A:A", 25)
            summary_ws.set_column("B:B", 20)
            
            # Apply header format
            summary_ws.write("A5", "Cost Element", header_format)
            summary_ws.write("B5", "Amount", header_format)
            
            # Apply special formatting to totals
            for i, row in enumerate(summary_data, start=6):
                if "Total" in row["Cost Element"]:
                    summary_ws.write(f"B{i}", row["Amount"], total_format)
                else:
                    summary_ws.write(f"B{i}", row["Amount"], currency_format)

            # Labor Detail Sheet
            if project_data.get("work_plan"):
                labor_rows = []
                for item in project_data["work_plan"]:
                    task = item.get("task", "Unknown Task")
                    hours_map = item.get("hours") or {}
                    
                    if isinstance(hours_map, dict) and hours_map:
                        for role, hrs in hours_map.items():
                            try:
                                hrs_num = float(hrs)
                            except (ValueError, TypeError):
                                hrs_num = 0.0
                            labor_rows.append({
                                "Task": task, 
                                "Role": str(role), 
                                "Hours": hrs_num
                            })
                    else:
                        labor_rows.append({
                            "Task": task, 
                            "Role": "TBD", 
                            "Hours": 0.0
                        })
                
                if labor_rows:
                    labor_df = pd.DataFrame(labor_rows)
                    labor_df.to_excel(writer, sheet_name="Labor Detail", index=False, startrow=2)
                    
                    labor_ws = writer.sheets["Labor Detail"]
                    labor_ws.merge_range("A1:C1", "Labor Breakdown", title_format)
                    labor_ws.set_column("A:A", 50)
                    labor_ws.set_column("B:B", 20)
                    labor_ws.set_column("C:C", 15)

            # Materials Detail Sheet
            materials = project_data.get("materials_and_tools", [])
            if materials:
                materials_df = pd.DataFrame(materials)
                materials_df.to_excel(writer, sheet_name="Materials Detail", index=False, startrow=2)
                
                materials_ws = writer.sheets["Materials Detail"]
                materials_ws.merge_range("A1:F1", "Materials & Tools", title_format)
                materials_ws.set_column("A:F", 20)

    except Exception as e:
        logger.error(f"Excel generation failed: {e}", exc_info=True)
        raise

    output_stream.seek(0)
    return output_stream

def create_boe_pdf(project_data: Dict[str, Any], totals: Dict[str, Any]) -> io.BytesIO:
    """Enhanced PDF generation with better formatting."""
    try:
        pdf = FPDF()
        pdf.add_page()
        
        # Title
        pdf.set_font("helvetica", "B", 20)
        pdf.cell(0, 15, "BASIS OF ESTIMATE", ln=True, align="C")
        pdf.ln(5)
        
        # Project info
        pdf.set_font("helvetica", "B", 12)
        pdf.cell(30, 8, "Project:", border=0)
        pdf.set_font("helvetica", "", 12)
        pdf.cell(0, 8, project_data.get("project_title", "N/A"), ln=True)
        
        pdf.set_font("helvetica", "B", 12)
        pdf.cell(30, 8, "Date:", border=0)
        pdf.set_font("helvetica", "", 12)
        pdf.cell(0, 8, datetime.now().strftime("%Y-%m-%d"), ln=True)
        
        pdf.set_font("helvetica", "B", 12)
        pdf.cell(30, 8, "Period:", border=0)
        pdf.set_font("helvetica", "", 12)
        pdf.cell(0, 8, project_data.get("pop", "TBD"), ln=True)
        
        pdf.ln(10)

        # Cost summary table
        table_data = [
            ("Direct Labor", totals.get("laborCost", 0)),
            ("Materials & Tools", totals.get("materialsCost", 0)),
            ("Travel", totals.get("travelCost", 0)),
            ("Subcontracts", totals.get("subcontractCost", 0)),
            ("Total Direct Costs", totals.get("totalDirectCosts", 0)),
            ("Overhead", totals.get("overheadAmount", 0)),
            ("G&A", totals.get("gnaAmount", 0)),
            ("Total Cost", totals.get("totalCost", 0)),
            ("Fee", totals.get("feeAmount", 0)),
            ("Total Proposed Price", totals.get("totalPrice", 0)),
        ]

        line_height = 8
        col_width = pdf.epw / 2
        
        # Table header
        pdf.set_font("helvetica", "B", 11)
        pdf.cell(col_width, line_height, "Cost Element", border=1, align="L")
        pdf.cell(col_width, line_height, "Amount", border=1, ln=True, align="R")
        
        # Table rows
        for label, value in table_data:
            if "Total" in label:
                pdf.set_font("helvetica", "B", 11)
            else:
                pdf.set_font("helvetica", "", 11)
            
            pdf.cell(col_width, line_height, label, border=1, align="L")
            pdf.cell(col_width, line_height, f"${value:,.2f}", border=1, ln=True, align="R")

        # Add footer
        pdf.ln(10)
        pdf.set_font("helvetica", "I", 10)
        pdf.cell(0, 5, f"Generated on {datetime.now().strftime('%Y-%m-%d at %H:%M')}", ln=True, align="R")

        return io.BytesIO(pdf.output(dest="S"))
        
    except Exception as e:
        logger.error(f"PDF generation failed: {e}", exc_info=True)
        raise

@app.route("/download/<path:filename>")
def download_file(filename):
    try:
        # Sanitize filename to prevent directory traversal
        safe_filename = os.path.basename(filename)
        file_path = os.path.join(REPORTS_DIR, safe_filename)
        
        if not os.path.exists(file_path):
            return jsonify({"error": "File not found"}), 404
        
        logger.info(f"Serving download: {safe_filename}")
        return send_from_directory(REPORTS_DIR, safe_filename, as_attachment=True)
        
    except Exception as e:
        logger.error(f"Download failed: {e}")
        return jsonify({"error": "Download failed"}), 500

# ======================================================================
# APPLICATION STARTUP
# ======================================================================

def initialize_application():
    """Initialize application with all required components."""
    try:
        # Validate configuration
        validate_configuration()
        
        # Start cleanup timer
        start_cleanup_timer()
        
        logger.info("🚀 Flask application initialized successfully")
        logger.info(f"Reports directory: {REPORTS_DIR}")
        logger.info(f"Job cleanup interval: {JOB_CLEANUP_INTERVAL}s")
        
    except Exception as e:
        logger.error(f"Application initialization failed: {e}")
        raise

# Initialize when module is imported
try:
    initialize_application()
except Exception as e:
    print(f"STARTUP ERROR: {e}")
    print("Application may not function correctly.")

if __name__ == "__main__":
    # Local development only (App Service uses gunicorn)
    port = int(os.getenv("PORT", "5001"))
    host = os.getenv("HOST", "0.0.0.0")
    debug = os.getenv("FLASK_DEBUG", "False").lower() == "true"
    
    logger.info(f"Starting development server on {host}:{port} (debug={debug})")
    app.run(debug=debug, port=port, host=host)
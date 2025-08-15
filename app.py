import os
import pandas as pd
from datetime import datetime
from flask import Flask, render_template, jsonify, request, send_from_directory, send_file
import io
import json
import logging
import threading
import uuid
import re
from xlsxwriter.workbook import Workbook
from typing import cast, Optional, Dict, Any
import time
from fpdf import FPDF

from tools import org_chart_parser
from tools import wbiops

from azure.ai.inference import ChatCompletionsClient
from azure.ai.inference.models import SystemMessage, UserMessage
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError, ServiceRequestError

from azure.storage.blob import BlobServiceClient
from openai import AsyncAzureOpenAI

# --- Configuration for AI Models ---
# GPT-4.x (planning / validation / final formatting)
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT")

# DeepSeek (main BoE estimator)
DEEPSEEK_ENDPOINT = os.getenv("DEEPSEEK_AZURE_ENDPOINT") or os.getenv("DEEPSEEK_ENDPOINT")
DEEPSEEK_KEY = os.getenv("DEEPSEEK_AZURE_KEY") or os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_DEPLOYMENT = os.getenv("DEEPSEEK_DEPLOYMENT", "WBI-Dash-DeepSeek")

def _ds_endpoint_base() -> str:
    # Accept either …/models or root; normalize to …/models
    base = (DEEPSEEK_ENDPOINT or "").rstrip("/")
    return base if base.endswith("/models") else f"{base}/models"

def _get_deepseek_client() -> ChatCompletionsClient:
    ep = _ds_endpoint_base()
    key = (DEEPSEEK_KEY or "").strip()
    if not ep or not key:
        raise RuntimeError("DeepSeek config missing (endpoint/key).")
    return ChatCompletionsClient(
        endpoint=ep,
        credential=AzureKeyCredential(key),
        api_version="2024-05-01-preview",
    )

def deepseek_complete(messages, max_tokens=2048, temperature=0.2, request_timeout=45):
    client = _get_deepseek_client()
    attempt = 0
    last_err = None
    while attempt < 2:
        try:
            return client.complete(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                model=(DEEPSEEK_DEPLOYMENT or "DeepSeek-R1-0528"),
                stream=False,
                # supported via pipeline policies in recent SDKs; if ignored, service default applies
                timeout=request_timeout,
            )
        except (HttpResponseError, ServiceRequestError) as e:
            last_err = e
            code = getattr(e, "status_code", None)
            if code in (429, 500, 502, 503, 504) or isinstance(e, ServiceRequestError):
                time.sleep(1.2 if attempt == 0 else 2.5)
                attempt += 1
                continue
            raise
    # final raise if both attempts failed
    raise last_err if last_err else RuntimeError("DeepSeek request failed")

print("Flask App Loaded. STORAGE:", os.environ.get("AZURE_STORAGE_CONNECTION_STRING"))

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

if not logging.getLogger().hasHandlers():
    logging.basicConfig(level=logging.INFO)

logger = logging.getLogger("wbi")
pipeline_jobs: Dict[str, Dict[str, Any]] = {}
estimate_jobs: Dict[str, Dict[str, Any]] = {}

# ==============================================================================
# CORE APP LOGIC
# ==============================================================================

def run_pipeline_logic(job_id: str) -> None:
    """Runs the WBI pipeline in a background thread and updates global status."""
    pipeline_jobs[job_id] = {
        "status": "running",
        "phase": "initializing",
        "log": [{"text": "✅ Pipeline Started"}],
        "opps_report_filename": None,
        "match_report_filename": None,
    }
    log = pipeline_jobs[job_id]["log"]

    try:
        pipeline_jobs[job_id]["phase"] = "scraping opportunities"
        pipeline_result = wbiops.run_wbi_pipeline(log)

        if pipeline_result:
            opps_df, matchmaking_df = pipeline_result
        else:
            log.append({"text": "❌ Pipeline did not return valid data."})
            opps_df, matchmaking_df = pd.DataFrame(), pd.DataFrame()

        pipeline_jobs[job_id]["phase"] = "generating reports"
        if not opps_df.empty:
            opps_filename = f"Opportunity_Report_{datetime.now():%Y-%m-%d_%H%M%S}.xlsx"
            opps_df.to_excel(os.path.join(REPORTS_DIR, opps_filename), index=False)
            pipeline_jobs[job_id]["opps_report_filename"] = opps_filename
            log.append({"text": f"📊 Primary Report Generated: {opps_filename}"})

        if not matchmaking_df.empty:
            match_filename = f"Strategic_Matchmaking_Report_{datetime.now():%Y-%m-%d_%H%M%S}.xlsx"
            matchmaking_df.to_excel(os.path.join(REPORTS_DIR, match_filename), index=False)
            pipeline_jobs[job_id]["match_report_filename"] = match_filename
            log.append({"text": f"🤝 Matchmaking Report Generated: {match_filename}"})

        pipeline_jobs[job_id]["phase"] = "completed"
        log.append({"text": "🎉 Run Complete!"})
        pipeline_jobs[job_id]["status"] = "completed"

    except Exception as e:
        logger.error(f"Pipeline job {job_id} failed: {e}", exc_info=True)
        log.append({"text": f"❌ Critical Error: {e}"})
        pipeline_jobs[job_id]["status"] = "failed"
        pipeline_jobs[job_id]["phase"] = "error"

def _run_boe_job(job_id: str, new_request: str, case_history: str) -> None:
    """Background BoE job: plan -> estimate -> finalize; updates estimate_jobs[job_id]."""
    estimate_jobs[job_id] = {"status": "running", "log": [], "result": None, "error": None}
    log = estimate_jobs[job_id]["log"]

    try:
        # 1) Planner (DeepSeek)
        log.append({"text": "Planning…"})
        plan_resp = deepseek_complete([
            SystemMessage(content="You are a strategic planner. Return a tight bullet list of BOE sections."),
            UserMessage(content=f"Create a high-level plan for a BOE based on this request:\n\n{new_request}")
        ], max_tokens=600, request_timeout=40)
        plan = (plan_resp.choices[0].message.content or "").strip()

        # 2) Estimator (DeepSeek) – ask for JSON explicitly
        log.append({"text": "Estimating…"})
        est_resp = deepseek_complete([
            SystemMessage(content=(
                "You are a senior cost estimator. Produce ONLY a JSON object with keys: "
                "'work_plan', 'materials_and_tools', 'travel', 'subcontracts'."
            )),
            UserMessage(content=(
                f"**Case History:**\n{case_history}\n\n"
                f"**High-Level Plan:**\n{plan}\n\n"
                f"**New Request:**\n{new_request}\n\n"
                f"**Your Task:** Generate the detailed JSON."
            ))
        ], max_tokens=2500, request_timeout=60)
        detailed = (est_resp.choices[0].message.content or "").strip()

        # 3) Finalizer (DeepSeek) – force single well-formed object
        log.append({"text": "Finalizing…"})
        final_resp = deepseek_complete([
            SystemMessage(content=(
                "You are a strict JSON formatter. Return ONE valid, minified JSON object only. "
                "Rules:\n"
                "- Output MUST be valid JSON (RFC 8259). No comments, no prose, no Markdown, no ellipses, no placeholders.\n"
                "- Use [] for unknown arrays, {} for unknown objects, 0 for unknown numbers, \"\" for unknown strings.\n"
                "- Keys to include at top level: project_title, start_date, pop, work_plan, materials_and_tools, travel, subcontracts.\n"
                "- work_plan is an array of {\"task\":\"\",\"hours\":{role:number,...}}.\n"
                "- materials_and_tools: array of {\"part_number\":\"\",\"description\":\"\",\"vendor\":\"\",\"quantity\":number,\"unit_cost\":number}.\n"
                "- travel: array of {\"purpose\":\"\",\"trips\":number,\"travelers\":number,\"days\":number,\"airfare\":number,\"lodging\":number,\"per_diem\":number}.\n"
                "- subcontracts: array of {\"subcontractor\":\"\",\"description\":\"\",\"cost\":number}.\n"
                "Return JSON only."
            )),

            UserMessage(content=(
                f"**Original Request:**\n{new_request}\n\n"
                f"**Detailed Estimation Data:**\n{detailed}\n\n"
                f"**Task:** Merge and return one JSON object."
            ))
        ], max_tokens=2500, request_timeout=60)

        final_json_str = (final_resp.choices[0].message.content or "").strip()
        result = _extract_json_from_response(final_json_str) or _try_lenient_json(final_json_str)
        if not result:
            raise ValueError("Model returned no valid JSON.")

        estimate_jobs[job_id]["result"] = result
        estimate_jobs[job_id]["status"] = "completed"
        log.append({"text": "Done."})

    except Exception as e:
        estimate_jobs[job_id]["status"] = "failed"
        estimate_jobs[job_id]["error"] = str(e)
        logger.error("BoE job %s failed: %s", job_id, e, exc_info=True)

def get_unique_pms():
    df, error = load_project_data()
    return sorted(df["pm"].dropna().unique().tolist()) if not error and not df.empty else []

def load_project_data():
    connect_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if not connect_str:
        logger.error("AZURE_STORAGE_CONNECTION_STRING is missing.")
        return pd.DataFrame(), "Azure Storage connection string not found."
    try:
        blob_service_client = BlobServiceClient.from_connection_string(connect_str)
        blob_client = blob_service_client.get_blob_client(BLOB_CONTAINER_NAME, PROJECT_DATA_FILE)
        with io.BytesIO() as stream:
            blob_client.download_blob().readinto(stream)
            stream.seek(0)
            df = pd.read_excel(stream)
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
            return pd.DataFrame(), f"Missing columns: {missing_cols}"
        df_filtered = df[list(column_mapping)].rename(columns=column_mapping)
        df_filtered["startDate"] = pd.to_datetime(df_filtered["startDate"], errors="coerce").dt.strftime("%Y-%m-%d")
        df_filtered["endDate"] = pd.to_datetime(df_filtered["endDate"], errors="coerce").dt.strftime("%Y-%m-%d")
        df_filtered.fillna("", inplace=True)
        return df_filtered, None
    except Exception as e:
        return pd.DataFrame(), str(e)

def _extract_json_from_response(text: str) -> Optional[Dict]:
    """Extract a JSON object from a string (fallback when models add prose)."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            logger.error(f"Failed to decode extracted JSON: {match.group(0)[:500]}")
            return None
    logger.error(f"No valid JSON object found in response: {text[:500]}")
    return None

def _try_lenient_json(text: str) -> Optional[Dict]:
    """Attempt to coerce near-JSON into valid JSON."""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    s = m.group(0)

    # Replace common junk the model emits
    s = re.sub(r"\{\s*\.\.\.\s*\}", "{}", s)
    s = re.sub(r"\[\s*\.\.\.\s*\]", "[]", s)
    s = s.replace("[calculated value]", "0")
    s = re.sub(r"\bNaN\b", "0", s)
    s = re.sub(r"(?m)^\s*//.*$", "", s)  # strip JS-style comments if any

    try:
        return json.loads(s)
    except Exception:
        return None

# ==============================================================================
# ASYNC AI HELPERS
# ==============================================================================

async def _call_ai_agent(
    client: AsyncAzureOpenAI,
    model_deployment: str,
    system_prompt: str,
    user_prompt: str,
    is_json: bool = False,
) -> str:
    """
    Generic call helper. Tries JSON mode first (if requested), then retries without it
    if the Azure endpoint rejects 'response_format'.
    """
    base_kwargs = dict(
        model=model_deployment,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,
        max_tokens=4096,
    )
    try:
        if is_json:
            response = await client.chat.completions.create(
                **base_kwargs,  # type: ignore[arg-type]
                response_format={"type": "json_object"},  # type: ignore[arg-type]
            )
        else:
            response = await client.chat.completions.create(**base_kwargs)  # type: ignore[arg-type]

        content = (response.choices[0].message.content or "").strip()
        if not content:
            raise ValueError("AI returned an empty response.")
        return content
    except Exception as e:
        if is_json:
            logger.warning("JSON-mode failed; retrying without response_format … (%s)", e)
            response = await client.chat.completions.create(**base_kwargs)  # type: ignore[arg-type]
            content = (response.choices[0].message.content or "").strip()
            if not content:
                raise ValueError("AI returned an empty response on retry.")
            return content
        raise

async def get_improved_ai_summary(description: str, update: str) -> str:
    """Asynchronously gets an improved summary for a project update via GPT-4.x."""
    # Make Pylance happy: ensure locals are concrete str
    endpoint_str: str = (AZURE_OPENAI_ENDPOINT or "").strip()
    key_str: str = (AZURE_OPENAI_KEY or "").strip()
    deployment_str: str = (AZURE_OPENAI_DEPLOYMENT or "").strip()

    if not all([endpoint_str, key_str, deployment_str]) or not update:
        logger.error("Azure OpenAI env vars missing or no update provided.")
        return "Azure OpenAI not configured or no update provided."

    system_prompt = (
        "You are an expert Project Manager who rewrites brief updates into clear, "
        "actionable, one-paragraph status summaries for an executive audience. Keep it concise."
    )
    user_prompt = (
        f"**Project Description:**\n{description}\n\n"
        f"**Brief Update:**\n{update}\n\n"
        f"**Rewritten Update:**"
    )

    client: Optional[AsyncAzureOpenAI] = None
    try:
        client = AsyncAzureOpenAI(
            azure_endpoint=endpoint_str,
            api_key=key_str,
            api_version="2024-02-01",
        )
        return await _call_ai_agent(client, deployment_str, system_prompt, user_prompt, is_json=False)
    finally:
        if client:
            await client.close()

# ==============================================================================
# FLASK ROUTES
# ==============================================================================

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/status")
def status():
    return "App is running"

@app.route("/api/run-pipeline", methods=["POST"])
def api_run_pipeline():
    job_id = str(uuid.uuid4())
    thread = threading.Thread(target=run_pipeline_logic, args=(job_id,), daemon=True)
    thread.start()
    return jsonify({"job_id": job_id})

@app.route("/api/pipeline-status/<job_id>", methods=["GET"])
def get_pipeline_status(job_id):
    job = pipeline_jobs.get(job_id)
    if job is None:
        return jsonify({"status": "not_found"}), 404
    return jsonify(job)

@app.route("/api/parse-org-chart", methods=["POST"])
def api_parse_org_chart():
    if "file" not in request.files or not request.files["file"].filename:
        return jsonify({"error": "No file provided"}), 400
    file = request.files["file"]
    try:
        output_filename = org_chart_parser.process_uploaded_pdf(file, REPORTS_DIR)
        if output_filename:
            return jsonify({"success": True, "filename": output_filename}), 200
        return jsonify({"error": "Processing failed"}), 500
    except Exception as e:
        logger.error(f"Org chart parsing failed: {e}", exc_info=True)
        return jsonify({"error": "Internal server error. Check logs for details."}), 500

@app.route("/api/pms")
def api_get_pms():
    return jsonify(get_unique_pms())

@app.route("/api/projects")
def api_get_projects():
    pm_name = request.args.get("pm")
    projects_df, error = load_project_data()
    if error:
        return jsonify({"error": error}), 500
    if not pm_name:
        return jsonify([])
    return jsonify(projects_df[projects_df["pm"] == pm_name].to_dict(orient="records"))

@app.route("/api/get_update")
def api_get_update():
    project_name = request.args.get("projectName")
    month = request.args.get("month")
    year_str = request.args.get("year")
    if not all([project_name, month, year_str]):
        return jsonify({})
    try:
        year = int(year_str or 0)
        connect_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
        if not connect_str:
            return jsonify({})
        blob_service_client = BlobServiceClient.from_connection_string(connect_str)
        blob_client = blob_service_client.get_blob_client(BLOB_CONTAINER_NAME, UPDATES_FILE)
        if not blob_client.exists():
            return jsonify({})

        with io.BytesIO() as stream:
            blob_client.download_blob().readinto(stream)
            stream.seek(0)
            updates_df = pd.read_csv(stream)

        updates_df["year"] = updates_df["year"].astype(int)
        update = updates_df[
            (updates_df["projectName"] == project_name)
            & (updates_df["month"] == month)
            & (updates_df["year"] == year)
        ]
        return jsonify(update.iloc[0].to_dict()) if not update.empty else jsonify({})
    except Exception as e:
        logger.error(f"Error retrieving update: {e}")
        return jsonify({})

@app.route("/api/update_project", methods=["POST"])
async def api_update_project():
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "Invalid request"}), 400

    try:
        ai_summary = await get_improved_ai_summary(data.get("description", ""), data["managerUpdate"])
        year_val = data.get("year")
        if year_val is None:
            return jsonify({"success": False, "error": "Year is a required field."}), 400

        new_entry = pd.DataFrame(
            [
                {
                    "projectName": data["projectName"],
                    "month": data["month"],
                    "year": int(year_val),
                    "managerUpdate": data["managerUpdate"],
                    "aiSummary": ai_summary,
                }
            ]
        )

        connect_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
        if not connect_str:
            return jsonify({"success": False, "error": "Storage not configured"}), 500

        blob_service_client = BlobServiceClient.from_connection_string(connect_str)
        blob_client = blob_service_client.get_blob_client(BLOB_CONTAINER_NAME, UPDATES_FILE)

        if blob_client.exists():
            with io.BytesIO() as stream:
                blob_client.download_blob().readinto(stream)
                stream.seek(0)
                updates_df = pd.read_csv(stream)
            updates_df["year"] = pd.to_numeric(updates_df["year"], errors="coerce").fillna(0).astype(int)
            updates_df = updates_df[
                ~(
                    (updates_df["projectName"] == data["projectName"])
                    & (updates_df["month"] == data["month"])
                    & (updates_df["year"] == int(year_val))
                )
            ]
            final_df = pd.concat([updates_df, new_entry], ignore_index=True)
        else:
            final_df = new_entry

        blob_client.upload_blob(final_df.to_csv(index=False), overwrite=True)
        return jsonify({"success": True, "aiSummary": ai_summary})
    except Exception as e:
        logger.error(f"Update save error: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Failed to save update."}), 500

@app.route("/api/rates", methods=["GET"])
def get_rates():
    try:
        rates_path = os.path.join(basedir, "tools", "rates.json")
        with open(rates_path, "r", encoding="utf-8") as f:
            rates_data = json.load(f)
        return jsonify(rates_data)
    except Exception as e:
        logger.error(f"Error loading rates.json: {e}")
        return jsonify({"error": "Could not load labor rates."}), 500

@app.route("/api/estimate", methods=["POST"])
def api_estimate_start():
    data = request.get_json() or {}
    new_request = data.get("new_request", "").strip()
    case_history = data.get("case_history", "").strip()
    if not new_request:
        return jsonify({"error": "New request data is missing."}), 400

    job_id = str(uuid.uuid4())
    t = threading.Thread(target=_run_boe_job, args=(job_id, new_request, case_history), daemon=True)
    t.start()

    # 202 Accepted with job id so the client can poll
    return jsonify({"job_id": job_id, "status": "queued"}), 202


@app.route("/api/estimate/<job_id>", methods=["GET"])
def api_estimate_status(job_id: str):
    job = estimate_jobs.get(job_id)
    if not job:
        return jsonify({"status": "not_found"}), 404
    # If completed, you get the result in the same payload
    return jsonify(job), 200

@app.route("/api/selftest/deepseek")
def selftest_deepseek():
    try:
        resp = deepseek_complete([
            SystemMessage(content="You echo JSON only."),
            UserMessage(content='Return {"ok":true,"model":"$MODEL"}')
        ], max_tokens=32, temperature=0)
        text = (resp.choices[0].message.content or "").strip()
        return jsonify({"ok": True, "raw": text})
    except Exception as e:
        logger.exception("DeepSeek selftest failed")
        return jsonify({"ok": False, "error": str(e)}), 500
    
@app.route("/api/selftest/aoai")
def selftest_aoai():
    from openai import AsyncAzureOpenAI
    import asyncio
    async def go():
        client = AsyncAzureOpenAI(
            azure_endpoint=(AZURE_OPENAI_ENDPOINT or "").strip(),
            api_key=(AZURE_OPENAI_KEY or "").strip(),
            api_version="2024-02-01",
        )
        try:
            resp = await client.chat.completions.create(
                model=(AZURE_OPENAI_DEPLOYMENT or "").strip(),
                messages=[
                    {"role": "system", "content": "You output JSON only."},
                    {"role": "user", "content": '{"ok":true}'}
                ],
                response_format={"type": "json_object"},
                max_tokens=10,
            )
            return (resp.choices[0].message.content or "").strip()
        finally:
            await client.close()
    try:
        out = asyncio.run(go())
        return jsonify({"ok": True, "raw": out})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/generate-boe-excel", methods=["POST"])
def generate_boe_excel():
    data = request.json
    if not data:
        return jsonify({"error": "Invalid request"}), 400
    project_data = data.get("projectData")
    totals = data.get("totals")
    if not project_data or not totals:
        return jsonify({"error": "Missing data"}), 400
    try:
        excel_stream = create_formatted_boe_excel(project_data, totals)
        project_title = project_data.get("project_title", "BoE_Report").replace(" ", "_")
        filename = f"BoE_{project_title}_Full.xlsx"
        return send_file(
            excel_stream,
            as_attachment=True,
            download_name=filename,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except Exception as e:
        logger.error(f"Failed to generate BoE Excel file: {e}", exc_info=True)
        return jsonify({"error": "Failed to generate Excel file."}), 500

@app.route("/api/generate-boe-pdf", methods=["POST"])
def generate_boe_pdf():
    data = request.json
    if not data:
        return jsonify({"error": "Invalid request"}), 400
    project_data = data.get("projectData")
    totals = data.get("totals")
    if not project_data or not totals:
        return jsonify({"error": "Missing data"}), 400
    try:
        pdf_stream = create_boe_pdf(project_data, totals)
        project_title = project_data.get("project_title", "BoE_Report").replace(" ", "_")
        filename = f"BoE_{project_title}_Customer.pdf"
        return send_file(pdf_stream, as_attachment=True, download_name=filename, mimetype="application/pdf")
    except Exception as e:
        logger.error(f"Failed to generate BoE PDF file: {e}", exc_info=True)
        return jsonify({"error": "Failed to generate PDF file."}), 500

def create_formatted_boe_excel(project_data, totals):
    output_stream = io.BytesIO()
    with pd.ExcelWriter(output_stream, engine="xlsxwriter") as writer:
        workbook = cast(Workbook, writer.book)
        title_format = workbook.add_format({"bold": True, "font_size": 14, "align": "left"})
        currency_format = workbook.add_format({"num_format": "$#,##0.00"})
        total_currency_format = workbook.add_format({"num_format": "$#,##0.00", "bold": True, "top": 1, "bottom": 1})

        summary_df = pd.DataFrame(
            [
                {"Cost Element": "Direct Labor", "Amount": totals["laborCost"]},
                {"Cost Element": "Materials & Tools", "Amount": totals["materialsCost"]},
                {"Cost Element": "Travel", "Amount": totals["travelCost"]},
                {"Cost Element": "Subcontracts", "Amount": totals["subcontractCost"]},
                {"Cost Element": "Total Direct Costs", "Amount": totals["totalDirectCosts"]},
                {"Cost Element": "Overhead", "Amount": totals["overheadAmount"]},
                {"Cost Element": "Subtotal", "Amount": totals["subtotal"]},
                {"Cost Element": "G&A", "Amount": totals["gnaAmount"]},
                {"Cost Element": "Total Cost", "Amount": totals["totalCost"]},
                {"Cost Element": "Fee", "Amount": totals["feeAmount"]},
                {"Cost Element": "Total Proposed Price", "Amount": totals["totalPrice"]},
            ]
        )
        summary_df.to_excel(writer, sheet_name="Cost Summary", index=False, startrow=3)
        summary_ws = writer.sheets["Cost Summary"]
        summary_ws.write("A1", f"BoE Summary: {project_data.get('project_title', 'N/A')}", title_format)
        summary_ws.set_column("A:A", 30)
        summary_ws.set_column("B:B", 20, currency_format)
        summary_ws.write("B9", totals["totalDirectCosts"], total_currency_format)
        summary_ws.write("B13", totals["totalPrice"], total_currency_format)

        if project_data.get("work_plan"):
            labor_rows = []
            for task in project_data["work_plan"]:
                base = {"WBS Element": task.get("task_name", "")}
                personnel = task.get("personnel") or []
                if personnel:
                    labor_rows.append({**base, **(personnel[0] or {})})
                else:
                    labor_rows.append(base)
            pd.DataFrame(labor_rows).to_excel(writer, sheet_name="Labor Detail", index=False)
            writer.sheets["Labor Detail"].set_column("A:A", 40)

        for sheet_name, data_key in [
            ("Materials & Tools", "materials_and_tools"),
            ("Travel", "travel"),
            ("Subcontracts", "subcontracts"),
        ]:
            if project_data.get(data_key):
                pd.DataFrame(project_data[data_key]).to_excel(writer, sheet_name=sheet_name, index=False)

    output_stream.seek(0)
    return output_stream

def create_boe_pdf(project_data, totals):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", "B", 20)
    pdf.cell(0, 10, "Basis of Estimate", ln=True, align="R")
    pdf.set_font("helvetica", "", 11)
    pdf.set_font("helvetica", "B", 11)
    pdf.cell(25, 8, "Project:", border=0)
    pdf.set_font("helvetica", "", 11)
    pdf.cell(0, 8, project_data.get("project_title", "N/A"), ln=True)
    pdf.set_font("helvetica", "B", 11)
    pdf.cell(25, 8, "Date:", border=0)
    pdf.set_font("helvetica", "", 11)
    pdf.cell(0, 8, datetime.now().strftime("%Y-%m-%d"), ln=True)
    pdf.ln(10)

    table_data = [
        ("Direct Labor", f"${totals.get('laborCost', 0):,.2f}"),
        ("Materials & Tools", f"${totals.get('materialsCost', 0):,.2f}"),
        ("Travel", f"${totals.get('travelCost', 0):,.2f}"),
        ("Subcontracts", f"${totals.get('subcontractCost', 0):,.2f}"),
        ("Total Direct Costs", f"${totals.get('totalDirectCosts', 0):,.2f}"),
        ("Indirect Costs (O/H + G&A)", f"${totals.get('overheadAmount', 0) + totals.get('gnaAmount', 0):,.2f}"),
        ("Total Estimated Cost", f"${totals.get('totalCost', 0):,.2f}"),
        ("Fee", f"${totals.get('feeAmount', 0):,.2f}"),
        ("Total Proposed Price", f"${totals.get('totalPrice', 0):,.2f}"),
    ]

    line_height = pdf.font_size * 2
    col_width = pdf.epw / 2
    pdf.set_font("helvetica", "B", 11)
    pdf.cell(col_width, line_height, "Cost Element", border=1)
    pdf.cell(col_width, line_height, "Amount", border=1, ln=True, align="R")
    pdf.set_font("helvetica", "", 11)

    for label, value in table_data:
        if "Total" in label:
            pdf.set_font("helvetica", "B", 11)
        pdf.cell(col_width, line_height, label, border=1)
        pdf.cell(col_width, line_height, value, border=1, ln=True, align="R")
        pdf.set_font("helvetica", "", 11)

    return io.BytesIO(pdf.output(dest="S"))

@app.route("/download/<path:filename>")
def download_file(filename):
    return send_from_directory(REPORTS_DIR, filename, as_attachment=True)

if __name__ == "__main__":
    # Local dev only (App Service uses gunicorn)
    app.run(debug=True, port=int(os.getenv("PORT", "5001")), host="0.0.0.0")

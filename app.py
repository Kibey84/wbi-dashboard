import os
import pandas as pd
from datetime import datetime
from flask import Flask, render_template, jsonify, request, send_from_directory, send_file
import io
import json
import asyncio
import logging
import threading
import uuid
import re
from xlsxwriter.workbook import Workbook
from typing import cast, Optional, Dict
from fpdf import FPDF

from tools import org_chart_parser
from tools import wbiops

from azure.storage.blob import BlobServiceClient
from openai import AsyncAzureOpenAI
# Import the specific type for response_format
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam, ChatCompletionMessage

# --- Configuration for AI Models ---
# For GPT-4 (used for planning, validation, and summaries)
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT")

# For DeepSeek (used for the main BoE estimation task)
DEEPSEEK_ENDPOINT = os.getenv("DEEPSEEK_AZURE_ENDPOINT")
DEEPSEEK_KEY = os.getenv("DEEPSEEK_AZURE_KEY")
DEEPSEEK_DEPLOYMENT = "WBI-Dash-DeepSeek"


print("Flask App Loaded. ENV:", os.environ.get("AZURE_STORAGE_CONNECTION_STRING"))

basedir = os.path.abspath(os.path.dirname(__file__))
app = Flask(__name__,
            template_folder=os.path.join(basedir, 'templates'),
            static_folder=os.path.join(basedir, 'static'))

REPORTS_DIR = os.path.join(os.getcwd(), "generated_reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

BLOB_CONTAINER_NAME = "data"
PROJECT_DATA_FILE = 'MockReportToolFile.xlsx'
UPDATES_FILE = 'updates.csv'

if not logging.getLogger().hasHandlers():
    logging.basicConfig(level=logging.INFO)

pipeline_jobs = {}

# ==============================================================================
# --- CORE APPLICATION LOGIC & HELPER FUNCTIONS ---
# ==============================================================================

def run_pipeline_logic(job_id):
    """
    Runs the WBI pipeline in a background thread and updates the global status with phases.
    """
    pipeline_jobs[job_id] = {
        "status": "running",
        "phase": "initializing",
        "log": [{"text": "✅ Pipeline Started"}],
        "opps_report_filename": None,
        "match_report_filename": None
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
            opps_filename = f"Opportunity_Report_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.xlsx"
            opps_df.to_excel(os.path.join(REPORTS_DIR, opps_filename), index=False)
            pipeline_jobs[job_id]["opps_report_filename"] = opps_filename
            log.append({"text": f"📊 Primary Report Generated: {opps_filename}"})

        if not matchmaking_df.empty:
            match_filename = f"Strategic_Matchmaking_Report_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.xlsx"
            matchmaking_df.to_excel(os.path.join(REPORTS_DIR, match_filename), index=False)
            pipeline_jobs[job_id]["match_report_filename"] = match_filename
            log.append({"text": f"🤝 Matchmaking Report Generated: {match_filename}"})

        pipeline_jobs[job_id]["phase"] = "completed"
        log.append({"text": "🎉 Run Complete!"})
        pipeline_jobs[job_id]["status"] = "completed"

    except Exception as e:
        logging.error(f"Pipeline job {job_id} failed: {e}", exc_info=True)
        log.append({"text": f"❌ Critical Error: {e}"})
        pipeline_jobs[job_id]["status"] = "failed"
        pipeline_jobs[job_id]["phase"] = "error"

def get_unique_pms():
    df, error = load_project_data()
    return sorted(df['pm'].dropna().unique().tolist()) if not error and not df.empty else []

def load_project_data():
    connect_str = os.getenv('AZURE_STORAGE_CONNECTION_STRING')
    if not connect_str:
        logging.error("AZURE_STORAGE_CONNECTION_STRING is missing.")
        return pd.DataFrame(), "Azure Storage connection string not found."
    try:
        blob_service_client = BlobServiceClient.from_connection_string(connect_str)
        blob_client = blob_service_client.get_blob_client(BLOB_CONTAINER_NAME, PROJECT_DATA_FILE)
        with io.BytesIO() as stream:
            blob_client.download_blob().readinto(stream)
            stream.seek(0)
            df = pd.read_excel(stream)
        column_mapping = {
            'project_id': 'projectName', 'project_pia': 'pi', 'project_owner': 'pm',
            'project_date_started': 'startDate', 'project_date_completed': 'endDate',
            'Status': 'status', 'project_description': 'description'
        }
        missing_cols = [col for col in column_mapping if col not in df.columns]
        if missing_cols:
            return pd.DataFrame(), f"Missing columns: {missing_cols}"
        df_filtered = df[list(column_mapping)].rename(columns=column_mapping)
        df_filtered['startDate'] = pd.to_datetime(df_filtered['startDate'], errors='coerce').dt.strftime('%Y-%m-%d')
        df_filtered['endDate'] = pd.to_datetime(df_filtered['endDate'], errors='coerce').dt.strftime('%Y-%m-%d')
        df_filtered.fillna('', inplace=True)
        return df_filtered, None
    except Exception as e:
        return pd.DataFrame(), str(e)

def _extract_json_from_response(text: str) -> Optional[Dict]:
    """Robustly extracts a JSON object from a string, even with surrounding text."""
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            logging.error(f"Failed to decode extracted JSON: {match.group(0)}")
            return None
    logging.error(f"No valid JSON object found in response: {text}")
    return None

# ==============================================================================
# --- ASYNCHRONOUS AI HELPER FUNCTIONS ---
# ==============================================================================

async def _call_ai_agent(client: AsyncAzureOpenAI, model_deployment: str, system_prompt: str, user_prompt: str, is_json: bool = False) -> str:
    """A generic, reusable function to call an AI agent."""
    try:
        response_format = {"type": "json_object"} if is_json else {"type": "text"}
        response = await client.chat.completions.create(
            model=model_deployment,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1,
            max_tokens=4096,
            response_format=response_format # type: ignore
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("AI returned an empty response.")
        return content
    except Exception as e:
        logging.error(f"Error calling AI agent with model {model_deployment}: {e}", exc_info=True)
        raise

async def get_improved_ai_summary(description, update):
    """Asynchronously gets an improved summary for a project update."""
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    key = os.getenv("AZURE_OPENAI_KEY")
    deployment_name = os.getenv("AZURE_OPENAI_DEPLOYMENT")

    if not all([endpoint, key, deployment_name]) or not update:
        logging.error("Azure OpenAI environment variables missing or no update provided.")
        return "Azure OpenAI not configured or no update provided."
    
    system_prompt = "You are an expert Project Manager... (rest of your prompt)"
    user_prompt = f"**Project Description:**\n{description}\n\n**Brief Update:**\n{update}\n\n**Rewritten Update:**"
    
    client = None
    try:
        assert endpoint is not None
        assert key is not None
        assert deployment_name is not None
        client = AsyncAzureOpenAI(azure_endpoint=endpoint, api_key=key, api_version="2024-02-01")
        return await _call_ai_agent(client, deployment_name, system_prompt, user_prompt)
    finally:
        if client:
            await client.close()

# ==============================================================================
# --- FLASK API ROUTES ---
# ==============================================================================

@app.route("/")
def index():
    return render_template('index.html')

@app.route("/status")
def status():
    return "App is running"

@app.route('/api/run-pipeline', methods=['POST'])
def api_run_pipeline():
    job_id = str(uuid.uuid4())
    thread = threading.Thread(target=run_pipeline_logic, args=(job_id,))
    thread.start()
    return jsonify({"job_id": job_id})

@app.route('/api/pipeline-status/<job_id>', methods=['GET'])
def get_pipeline_status(job_id):
    job = pipeline_jobs.get(job_id)
    if job is None:
        return jsonify({"status": "not_found"}), 404
    return jsonify(job)

@app.route('/api/parse-org-chart', methods=['POST'])
def api_parse_org_chart():
    if 'file' not in request.files or not request.files['file'].filename:
        return jsonify({"error": "No file provided"}), 400
    file = request.files['file']
    try:
        output_filename = org_chart_parser.process_uploaded_pdf(file, REPORTS_DIR)
        return jsonify({"success": True, "filename": output_filename}) if output_filename else jsonify({"error": "Processing failed"}), 500
    except Exception as e:
        logging.error(f"Org chart parsing failed: {e}", exc_info=True)
        return jsonify({"error": "Internal server error. Check logs for details."}), 500

@app.route('/api/pms')
def api_get_pms():
    return jsonify(get_unique_pms())

@app.route('/api/projects')
def api_get_projects():
    pm_name = request.args.get('pm')
    projects_df, error = load_project_data()
    if error:
        return jsonify({"error": error}), 500
    return jsonify(projects_df[projects_df['pm'] == pm_name].to_dict(orient='records')) if pm_name else jsonify([])

@app.route('/api/get_update')
def api_get_update():
    project_name = request.args.get('projectName')
    month = request.args.get('month')
    year_str = request.args.get('year')
    if not all([project_name, month, year_str]):
        return jsonify({})
    try:
        year = int(year_str or 0)
        connect_str = os.getenv('AZURE_STORAGE_CONNECTION_STRING')
        if not connect_str: return jsonify({})
        blob_service_client = BlobServiceClient.from_connection_string(connect_str)
        blob_client = blob_service_client.get_blob_client(BLOB_CONTAINER_NAME, UPDATES_FILE)
        if not blob_client.exists(): return jsonify({})
        
        with io.BytesIO() as stream:
            blob_client.download_blob().readinto(stream)
            stream.seek(0)
            updates_df = pd.read_csv(stream)
        
        updates_df['year'] = updates_df['year'].astype(int)
        update = updates_df[(updates_df['projectName'] == project_name) & (updates_df['month'] == month) & (updates_df['year'] == year)]
        return jsonify(update.iloc[0].to_dict()) if not update.empty else jsonify({})
    except Exception as e:
        logging.error(f"Error retrieving update: {e}")
        return jsonify({})

@app.route('/api/update_project', methods=['POST'])
async def api_update_project():
    data = request.get_json()
    if not data: return jsonify({"success": False, "error": "Invalid request"}), 400
    
    try:
        ai_summary = await get_improved_ai_summary(data.get('description', ''), data['managerUpdate'])
        assert data['year'] is not None
        new_entry = pd.DataFrame([{'projectName': data['projectName'], 'month': data['month'], 'year': int(data['year']), 'managerUpdate': data['managerUpdate'], 'aiSummary': ai_summary}])
        
        connect_str = os.getenv('AZURE_STORAGE_CONNECTION_STRING')
        if not connect_str: return jsonify({"success": False, "error": "Storage not configured"}), 500
        
        blob_service_client = BlobServiceClient.from_connection_string(connect_str)
        blob_client = blob_service_client.get_blob_client(BLOB_CONTAINER_NAME, UPDATES_FILE)
        
        if blob_client.exists():
            with io.BytesIO() as stream:
                blob_client.download_blob().readinto(stream)
                stream.seek(0)
                updates_df = pd.read_csv(stream)
            updates_df['year'] = pd.to_numeric(updates_df['year'], errors='coerce').fillna(0).astype(int)
            updates_df = updates_df[~((updates_df['projectName'] == data['projectName']) & (updates_df['month'] == data['month']) & (updates_df['year'] == int(data['year'])))]
            final_df = pd.concat([updates_df, new_entry], ignore_index=True)
        else:
            final_df = new_entry
            
        blob_client.upload_blob(final_df.to_csv(index=False), overwrite=True)
        return jsonify({"success": True, "aiSummary": ai_summary})
    except Exception as e:
        logging.error(f"Update save error: {e}")
        return jsonify({"success": False, "error": "Failed to save update."}), 500

@app.route('/api/rates', methods=['GET'])
def get_rates():
    try:
        rates_path = os.path.join(basedir, 'tools', 'rates.json')
        with open(rates_path, 'r') as f:
            rates_data = json.load(f)
        return jsonify(rates_data)
    except Exception as e:
        logging.error(f"Error loading rates.json: {e}")
        return jsonify({"error": "Could not load labor rates."}), 500

@app.route('/api/estimate', methods=['POST'])
async def api_estimate_boe():
    """Orchestrates a team of AI agents to generate a Basis of Estimate."""
    if not all([AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY, AZURE_OPENAI_DEPLOYMENT, DEEPSEEK_ENDPOINT, DEEPSEEK_KEY]):
        return jsonify({"error": "Server configuration error: AI credentials missing."}), 500

    data = request.get_json()
    if not data: return jsonify({"error": "Invalid request"}), 400
    
    new_request = data.get("new_request", "")
    case_history = data.get("case_history", "")
    if not new_request: return jsonify({"error": "New request data is missing."}), 400

    gpt4_client, deepseek_client = None, None
    try:
        assert AZURE_OPENAI_ENDPOINT is not None
        assert AZURE_OPENAI_KEY is not None
        assert AZURE_OPENAI_DEPLOYMENT is not None
        assert DEEPSEEK_ENDPOINT is not None
        assert DEEPSEEK_KEY is not None
        assert DEEPSEEK_DEPLOYMENT is not None
        
        gpt4_client = AsyncAzureOpenAI(azure_endpoint=AZURE_OPENAI_ENDPOINT, api_key=AZURE_OPENAI_KEY, api_version="2024-02-01")
        deepseek_client = AsyncAzureOpenAI(azure_endpoint=DEEPSEEK_ENDPOINT, api_key=DEEPSEEK_KEY, api_version="2024-02-01")

        planner_system_prompt = "You are a strategic planner. Your job is to analyze a user's request and create a high-level, bulleted list of the key sections needed for a comprehensive Basis of Estimate (BOE)."
        planner_user_prompt = f"Create a high-level plan for a BOE based on this request:\n\n{new_request}"
        plan = await _call_ai_agent(gpt4_client, AZURE_OPENAI_DEPLOYMENT, planner_system_prompt, planner_user_prompt)
        logging.info(f"AI Planner Output:\n{plan}")

        estimator_system_prompt = "You are a senior cost estimator. Your job is to take a plan and a new request, analyze them against the provided case history, and generate the detailed line items for the BOE. Provide ONLY a JSON object with keys: 'work_plan', 'materials_and_tools', 'travel', 'subcontracts'."
        estimator_user_prompt = f"**Case History:**\n{case_history}\n\n**High-Level Plan:**\n{plan}\n\n**New Request:**\n{new_request}\n\n**Your Task:** Generate the detailed JSON data for the work plan, materials, travel, and subcontracts."
        detailed_estimation_str = await _call_ai_agent(deepseek_client, DEEPSEEK_DEPLOYMENT, estimator_system_prompt, estimator_user_prompt, is_json=True)
        
        finalizer_system_prompt = "You are a data formatting specialist. Your only job is to take the provided text and ensure it is a single, perfectly structured JSON object that matches the required final BoE format. Add any missing top-level keys like 'project_title' or 'assumptions' based on the content."
        finalizer_user_prompt = f"**Original Request:**\n{new_request}\n\n**Detailed Estimation Data:**\n{detailed_estimation_str}\n\n**Your Task:** Combine all information into a single, valid JSON object matching the final BoE structure."
        final_json_str = await _call_ai_agent(gpt4_client, AZURE_OPENAI_DEPLOYMENT, finalizer_system_prompt, finalizer_user_prompt, is_json=True)
        
        final_json = _extract_json_from_response(final_json_str)
        if not final_json:
            return jsonify({"error": "The final AI agent failed to produce a valid JSON object."}), 500

        return jsonify(final_json), 200

    except Exception as e:
        logging.error(f"Error in /api/estimate: {e}", exc_info=True)
        return jsonify({"error": "An internal server error occurred during AI processing."}), 500
    finally:
        if gpt4_client: await gpt4_client.close()
        if deepseek_client: await deepseek_client.close()

@app.route('/api/generate-boe-excel', methods=['POST'])
def generate_boe_excel():
    data = request.json
    if not data: return jsonify({"error": "Invalid request"}), 400
    project_data = data.get('projectData'); totals = data.get('totals')
    if not project_data or not totals: return jsonify({"error": "Missing data"}), 400
    try:
        excel_stream = create_formatted_boe_excel(project_data, totals)
        project_title = project_data.get('project_title', 'BoE_Report').replace(' ', '_')
        filename = f"BoE_{project_title}_Full.xlsx"
        return send_file(excel_stream, as_attachment=True, download_name=filename, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    except Exception as e:
        logging.error(f"Failed to generate BoE Excel file: {e}", exc_info=True)
        return jsonify({"error": "Failed to generate Excel file."}), 500

@app.route('/api/generate-boe-pdf', methods=['POST'])
def generate_boe_pdf():
    data = request.json
    if not data: return jsonify({"error": "Invalid request"}), 400
    project_data = data.get('projectData'); totals = data.get('totals')
    if not project_data or not totals: return jsonify({"error": "Missing data"}), 400
    try:
        pdf_stream = create_boe_pdf(project_data, totals)
        project_title = project_data.get('project_title', 'BoE_Report').replace(' ', '_')
        filename = f"BoE_{project_title}_Customer.pdf"
        return send_file(pdf_stream, as_attachment=True, download_name=filename, mimetype='application/pdf')
    except Exception as e:
        logging.error(f"Failed to generate BoE PDF file: {e}", exc_info=True)
        return jsonify({"error": "Failed to generate PDF file."}), 500

def create_formatted_boe_excel(project_data, totals):
    output_stream = io.BytesIO()
    with pd.ExcelWriter(output_stream, engine='xlsxwriter') as writer:
        workbook = cast(Workbook, writer.book)
        title_format = workbook.add_format({'bold': True, 'font_size': 14, 'align': 'left'})
        currency_format = workbook.add_format({'num_format': '$#,##0.00'})
        total_currency_format = workbook.add_format({'num_format': '$#,##0.00', 'bold': True, 'top': 1, 'bottom': 1})
        
        summary_df = pd.DataFrame([
            {"Cost Element": "Direct Labor", "Amount": totals['laborCost']}, {"Cost Element": "Materials & Tools", "Amount": totals['materialsCost']},
            {"Cost Element": "Travel", "Amount": totals['travelCost']}, {"Cost Element": "Subcontracts", "Amount": totals['subcontractCost']},
            {"Cost Element": "Total Direct Costs", "Amount": totals['totalDirectCosts']}, {"Cost Element": "Overhead", "Amount": totals['overheadAmount']},
            {"Cost Element": "Subtotal", "Amount": totals['subtotal']}, {"Cost Element": "G&A", "Amount": totals['gnaAmount']},
            {"Cost Element": "Total Cost", "Amount": totals['totalCost']}, {"Cost Element": "Fee", "Amount": totals['feeAmount']},
            {"Cost Element": "Total Proposed Price", "Amount": totals['totalPrice']},
        ])
        summary_df.to_excel(writer, sheet_name='Cost Summary', index=False, startrow=3)
        summary_ws = writer.sheets['Cost Summary']
        summary_ws.write('A1', f"BoE Summary: {project_data.get('project_title', 'N/A')}", title_format)
        summary_ws.set_column('A:A', 30); summary_ws.set_column('B:B', 20, currency_format)
        summary_ws.write('B9', totals['totalDirectCosts'], total_currency_format)
        summary_ws.write('B13', totals['totalPrice'], total_currency_format)
        
        if project_data.get('work_plan'):
            labor_data = [{'WBS Element': task['task_name'], **task['personnel'][0]} for task in project_data['work_plan']]
            pd.DataFrame(labor_data).to_excel(writer, sheet_name='Labor Detail', index=False)
            writer.sheets['Labor Detail'].set_column('A:A', 40)

        for sheet_name, data_key in [('Materials & Tools', 'materials_and_tools'), ('Travel', 'travel'), ('Subcontracts', 'subcontracts')]:
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
    pdf.cell(0, 8, project_data.get('project_title', 'N/A'), ln=True)
    pdf.set_font("helvetica", "B", 11)
    pdf.cell(25, 8, "Date:", border=0)
    pdf.set_font("helvetica", "", 11)
    pdf.cell(0, 8, datetime.now().strftime('%Y-%m-%d'), ln=True)
    pdf.ln(10) 
    
    table_data = [
        ("Direct Labor", f"${totals.get('laborCost', 0):,.2f}"), ("Materials & Tools", f"${totals.get('materialsCost', 0):,.2f}"),
        ("Travel", f"${totals.get('travelCost', 0):,.2f}"), ("Subcontracts", f"${totals.get('subcontractCost', 0):,.2f}"),
        ("Total Direct Costs", f"${totals.get('totalDirectCosts', 0):,.2f}"),
        ("Indirect Costs (O/H + G&A)", f"${totals.get('overheadAmount', 0) + totals.get('gnaAmount', 0):,.2f}"),
        ("Total Estimated Cost", f"${totals.get('totalCost', 0):,.2f}"), ("Fee", f"${totals.get('feeAmount', 0):,.2f}"),
        ("Total Proposed Price", f"${totals.get('totalPrice', 0):,.2f}")
    ]
    
    line_height = pdf.font_size * 2
    col_width = pdf.epw / 2  
    pdf.set_font("helvetica", "B", 11)
    pdf.cell(col_width, line_height, "Cost Element", border=1)
    pdf.cell(col_width, line_height, "Amount", border=1, ln=True, align='R')
    pdf.set_font("helvetica", "", 11)

    for i, (label, value) in enumerate(table_data):
        if "Total" in label: pdf.set_font("helvetica", "B", 11)
        pdf.cell(col_width, line_height, label, border=1)
        pdf.cell(col_width, line_height, value, border=1, ln=True, align='R')
        pdf.set_font("helvetica", "", 11)

    return io.BytesIO(pdf.output(dest='S'))

@app.route('/download/<path:filename>')
def download_file(filename):
    return send_from_directory(REPORTS_DIR, filename, as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True, port=5001, host='0.0.0.0')

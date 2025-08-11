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

# --- Configuration for AI Models ---
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT")

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
            'project_id': 'projectName',
            'project_pia': 'pi',
            'project_owner': 'pm',
            'project_date_started': 'startDate',
            'project_date_completed': 'endDate',
            'Status': 'status',
            'project_description': 'description'
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

def get_ai_summary(description, update):
    return asyncio.run(get_improved_ai_summary(description, update))

async def get_improved_ai_summary(description, update):
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    key = os.getenv("AZURE_OPENAI_KEY")
    deployment_name = os.getenv("AZURE_OPENAI_DEPLOYMENT")

    if not all([endpoint, key, deployment_name]) or not update:
        logging.error("Azure OpenAI environment variables missing or no update provided.")
        return "Azure OpenAI not configured or no update provided."
    
    assert deployment_name is not None
    assert endpoint is not None
    assert key is not None

    system_prompt = """
You are an expert Project Manager at WBI writing an executive summary for a monthly status report. Your task is to take a project description (for context) and a brief update and rewrite it into a professional, concise, and impactful summary.

Crucially, you must explicitly highlight WBI's contributions and role in the progress described. Frame the update from the perspective of what WBI has accomplished or is currently doing.
"""

    user_prompt = f"""
**Project Description (for context):**
{description}

**Brief Monthly Update to Improve:**
{update}

**Rewritten Update (Highlighting WBI's Contributions):**
"""

    try:
        client = AsyncAzureOpenAI(
            azure_endpoint=endpoint,
            api_key=key,
            api_version="2024-02-01"
        )

        response = await client.chat.completions.create(
            model=deployment_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
        )

        if response.choices and response.choices[0].message and response.choices[0].message.content:
            return response.choices[0].message.content.strip()

        return "AI returned no content."

    except Exception as e:
        logging.error(f"Azure OpenAI Error: {e}")
        return f"Error communicating with AI service: {e}"

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
# --- FLASK API ROUTES ---
# ==============================================================================

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
    return jsonify({
        "status": job["status"],
        "phase": job.get("phase", "unknown"),
        "log": job.get("log", []),
        "opps_report_filename": job.get("opps_report_filename"),
        "match_report_filename": job.get("match_report_filename")
    })

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
    if not project_name or not month or not (year_str and year_str.isdigit()):
        return jsonify({})
    year = int(year_str)
    connect_str = os.getenv('AZURE_STORAGE_CONNECTION_STRING')
    if not connect_str:
        logging.error("Azure Storage connection string missing.")
        return jsonify({})
    try:
        blob_service_client = BlobServiceClient.from_connection_string(connect_str)
        blob_client = blob_service_client.get_blob_client(BLOB_CONTAINER_NAME, UPDATES_FILE)
        if not blob_client.exists():
            return jsonify({})
        with io.BytesIO() as stream:
            blob_client.download_blob().readinto(stream)
            stream.seek(0)
            updates_df = pd.read_csv(stream)
        updates_df['year'] = updates_df['year'].astype(int)
        update = updates_df[
            (updates_df['projectName'] == project_name) &
            (updates_df['month'] == month) &
            (updates_df['year'] == year)
        ]
        return jsonify(update.iloc[0].to_dict()) if not update.empty else jsonify({})
    except Exception as e:
        logging.error(f"Error retrieving update: {e}")
        return jsonify({})

@app.route('/api/update_project', methods=['POST'])
def api_update_project():
    data = request.json
    if not data:
        return jsonify({"success": False, "error": "Invalid request"}), 400
    required_fields = ['projectName', 'month', 'year', 'managerUpdate']
    if not all(field in data for field in required_fields):
        return jsonify({"success": False, "error": "Missing fields"}), 400
    try:
        year = int(data['year'])
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "Invalid year"}), 400

    ai_summary = get_ai_summary(data.get('description', ''), data['managerUpdate'])
    new_entry = pd.DataFrame([{
        'projectName': data['projectName'],
        'month': data['month'],
        'year': year,
        'managerUpdate': data['managerUpdate'],
        'aiSummary': ai_summary
    }])
    connect_str = os.getenv('AZURE_STORAGE_CONNECTION_STRING')
    if not connect_str:
        return jsonify({"success": False, "error": "Storage connection string missing."}), 500
    try:
        blob_service_client = BlobServiceClient.from_connection_string(connect_str)
        blob_client = blob_service_client.get_blob_client(BLOB_CONTAINER_NAME, UPDATES_FILE)
        if blob_client.exists():
            with io.BytesIO() as stream:
                blob_client.download_blob().readinto(stream)
                stream.seek(0)
                updates_df = pd.read_csv(stream)
            updates_df['year'] = pd.to_numeric(updates_df['year'], errors='coerce').fillna(0).astype(int)
            updates_df = updates_df[~(
                (updates_df['projectName'] == data['projectName']) &
                (updates_df['month'] == data['month']) &
                (updates_df['year'] == year)
            )]
            final_df = pd.concat([updates_df, new_entry], ignore_index=True)
        else:
            final_df = new_entry
        blob_client.upload_blob(final_df.to_csv(index=False), overwrite=True)
        return jsonify({"success": True, "aiSummary": ai_summary})
    except Exception as e:
        logging.error(f"Update save error: {e}")
        return jsonify({"success": False, "error": "Failed to save update."}), 500

@app.route('/download/<path:filename>')
def download_file(filename):
    return send_from_directory(REPORTS_DIR, filename, as_attachment=True)

@app.route('/')
def index():
    return render_template('index.html')

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
    """
    Receives proposal data and returns a Basis of Estimate from the DeepSeek model.
    """
    if not all([DEEPSEEK_ENDPOINT, DEEPSEEK_KEY]):
        logging.error("DeepSeek AI credentials not fully set.")
        return jsonify({"error": "Server configuration error: AI credentials missing."}), 500

    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid request: No JSON body received."}), 400

    case_history = data.get("case_history", "")
    new_request = data.get("new_request", "")
    if not new_request:
        return jsonify({"error": "New request data is missing."}), 400

    client = None
    try:
        assert DEEPSEEK_ENDPOINT is not None
        assert DEEPSEEK_KEY is not None
        client = AsyncAzureOpenAI(azure_endpoint=DEEPSEEK_ENDPOINT, api_key=DEEPSEEK_KEY, api_version="2024-02-01")

        system_prompt = "You are an expert government contract proposal manager. Your task is to generate a detailed Basis of Estimate (BOE) in JSON format."
        user_prompt = f"""
        Analyze the 'New Request' based on the 'Case History' provided.
        Generate a comprehensive Basis of Estimate (BOE).
        The response MUST be a single, valid JSON object and nothing else.

        Your JSON output must follow this exact structure:
        {{
          "project_title": "<The title of the new request>",
          "period_of_performance_months": <integer>,
          "assumptions": [
            "<A list of key assumptions made>"
          ],
          "work_plan": [
            {{
              "task_name": "<Descriptive name of the task>",
              "personnel": [
                {{
                  "role": "<Personnel role>",
                  "hours": <integer>
                }}
              ]
            }}
          ],
          "materials_and_tools": [
            {{
              "part_number": "<Part number or identifier>",
              "description": "<Item description>",
              "vendor": "<Vendor name>",
              "quantity": <integer>,
              "unit_cost": <float>
            }}
          ],
          "travel": [
            {{
              "description": "<Purpose of the trip>",
              "destination": "<City, State>",
              "duration_days": <integer>,
              "num_travelers": <integer>,
              "estimated_cost": <float>
            }}
          ],
          "subcontracts": [
            {{
              "subcontractor_name": "<Name of the subcontractor>",
              "scope_of_work": "<Detailed description of their work>",
              "estimated_cost": <float>
            }}
          ]
        }}

        --- CASE HISTORY (FOR REFERENCE) ---
        {case_history}

        --- NEW REQUEST (FOR BOE GENERATION) ---
        {new_request}
        """
        
        assert DEEPSEEK_DEPLOYMENT is not None
        response = await client.chat.completions.create(
            model=DEEPSEEK_DEPLOYMENT,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1,
            max_tokens=4096,
            response_format={"type": "json_object"}
        )
        
        raw_content = response.choices[0].message.content
        if not raw_content:
            raise ValueError("AI returned an empty response.")

        final_json = json.loads(raw_content)
        return jsonify(final_json), 200

    except Exception as e:
        logging.error(f"Error in /api/estimate: {e}", exc_info=True)
        return jsonify({"error": "An internal server error occurred during AI processing."}), 500
    finally:
        if client:
            await client.close()

@app.route('/api/generate-boe-excel', methods=['POST'])
def generate_boe_excel():
    data = request.json
    if not data:
        return jsonify({"error": "Invalid request: No JSON body received."}), 400

    project_data = data.get('projectData')
    totals = data.get('totals')

    if not project_data or not totals:
        return jsonify({"error": "Missing project data or totals."}), 400

    try:
        excel_stream = create_formatted_boe_excel(project_data, totals)

        project_title = project_data.get('project_title', 'BoE_Report').replace(' ', '_')
        filename = f"BoE_{project_title}_Full.xlsx"

        return send_file(
            excel_stream,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        logging.error(f"Failed to generate BoE Excel file: {e}", exc_info=True)
        return jsonify({"error": "Failed to generate Excel file."}), 500

@app.route('/api/generate-boe-pdf', methods=['POST'])
def generate_boe_pdf():
    data = request.json
    if not data:
        return jsonify({"error": "Invalid request"}), 400

    project_data = data.get('projectData')
    totals = data.get('totals')
    if not project_data or not totals:
        return jsonify({"error": "Missing project data or totals."}), 400

    try:
        pdf_stream = create_boe_pdf(project_data, totals)

        project_title = project_data.get('project_title', 'BoE_Report').replace(' ', '_')
        filename = f"BoE_{project_title}_Customer.pdf"

        return send_file(
            pdf_stream,
            as_attachment=True,
            download_name=filename,
            mimetype='application/pdf'
        )
    except Exception as e:
        logging.error(f"Failed to generate BoE PDF file: {e}", exc_info=True)
        return jsonify({"error": "Failed to generate PDF file."}), 500

def create_formatted_boe_excel(project_data, totals):
    output_stream = io.BytesIO()
    with pd.ExcelWriter(output_stream, engine='xlsxwriter', engine_kwargs={"options": {}}) as writer:
        workbook = cast(Workbook, writer.book)
        title_format = workbook.add_format({'bold': True, 'font_size': 14, 'align': 'left'})
        currency_format = workbook.add_format({'num_format': '$#,##0.00'})
        total_currency_format = workbook.add_format({'num_format': '$#,##0.00', 'bold': True, 'top': 1, 'bottom': 1})
        summary_df = pd.DataFrame([
            {"Cost Element": "Direct Labor", "Amount": totals['laborCost']},
            {"Cost Element": "Materials & Tools", "Amount": totals['materialsCost']},
            {"Cost Element": "Travel", "Amount": totals['travelCost']},
            {"Cost Element": "Subcontracts", "Amount": totals['subcontractCost']},
            {"Cost Element": "Total Direct Costs", "Amount": totals['totalDirectCosts']},
            {"Cost Element": "Overhead", "Amount": totals['overheadAmount']},
            {"Cost Element": "Subtotal", "Amount": totals['subtotal']},
            {"Cost Element": "G&A", "Amount": totals['gnaAmount']},
            {"Cost Element": "Total Cost", "Amount": totals['totalCost']},
            {"Cost Element": "Fee", "Amount": totals['feeAmount']},
            {"Cost Element": "Total Proposed Price", "Amount": totals['totalPrice']},
        ])
        summary_df.to_excel(writer, sheet_name='Cost Summary', index=False, startrow=3)
        summary_ws = writer.sheets['Cost Summary']
        summary_ws.write('A1', f"BoE Summary: {project_data.get('project_title', 'N/A')}", title_format)
        summary_ws.set_column('A:A', 30, None)
        summary_ws.set_column('B:B', 20, currency_format)
        summary_ws.write('B9', totals['totalDirectCosts'], total_currency_format)
        summary_ws.write('B13', totals['totalPrice'], total_currency_format)
        if project_data.get('work_plan'):
            labor_data = []
            for task in project_data['work_plan']:
                row = {'WBS Element': task['task']}
                row.update(task['hours'])
                labor_data.append(row)
            labor_df = pd.DataFrame(labor_data)
            labor_df.to_excel(writer, sheet_name='Labor Detail', index=False)
            labor_ws = writer.sheets['Labor Detail']
            labor_ws.set_column('A:A', 40, None)
        if project_data.get('materials_and_tools'):
            pd.DataFrame(project_data['materials_and_tools']).to_excel(writer, sheet_name='Materials & Tools', index=False)
        if project_data.get('travel'):
            pd.DataFrame(project_data['travel']).to_excel(writer, sheet_name='Travel', index=False)
        if project_data.get('subcontracts'):
            pd.DataFrame(project_data['subcontracts']).to_excel(writer, sheet_name='Subcontracts', index=False)
    output_stream.seek(0)
    return output_stream

def create_boe_pdf(project_data, totals):
    """
    Creates a BoE Summary PDF file in memory.
    """
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
        ("Direct Labor", f"${totals.get('laborCost', 0):,.2f}"),
        ("Materials & Tools", f"${totals.get('materialsCost', 0):,.2f}"),
        ("Travel", f"${totals.get('travelCost', 0):,.2f}"),
        ("Subcontracts", f"${totals.get('subcontractCost', 0):,.2f}"),
        ("Total Direct Costs", f"${totals.get('totalDirectCosts', 0):,.2f}"),
        ("Indirect Costs (O/H + G&A)", f"${totals.get('overheadAmount', 0) + totals.get('gnaAmount', 0):,.2f}"),
        ("Total Estimated Cost", f"${totals.get('totalCost', 0):,.2f}"),
        ("Fee", f"${totals.get('feeAmount', 0):,.2f}"),
        ("Total Proposed Price", f"${totals.get('totalPrice', 0):,.2f}")
    ]

    line_height = pdf.font_size * 2
    col_width = pdf.epw / 2  

    pdf.set_font("helvetica", "B", 11)
    pdf.cell(col_width, line_height, "Cost Element", border=1)
    pdf.cell(col_width, line_height, "Amount", border=1, ln=True, align='R')
    pdf.set_font("helvetica", "", 11)

    for i, (label, value) in enumerate(table_data):
        if "Total" in label:
            pdf.set_font("helvetica", "B", 11)

        pdf.cell(col_width, line_height, label, border=1)
        pdf.cell(col_width, line_height, value, border=1, ln=True, align='R')

        pdf.set_font("helvetica", "", 11)

    pdf_stream = io.BytesIO(pdf.output(dest='S'))
    return pdf_stream

if __name__ == '__main__':
    app.run(debug=True, port=5001, host='0.0.0.0')

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
from typing import cast

from tools import org_chart_parser
from tools import wbiops

from azure.storage.blob import BlobServiceClient
from azure.ai.inference.aio import ChatCompletionsClient
from azure.ai.inference.models import SystemMessage, UserMessage
from azure.core.credentials import AzureKeyCredential

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

def run_pipeline_logic(job_id):
    """
    This function runs the WBI pipeline in a background thread.
    It updates the global `pipeline_jobs` dictionary with its status.
    """
    pipeline_jobs[job_id] = {
        "status": "running",
        "log": [{"text": "✅ Pipeline Started"}],
        "opps_report_filename": None,
        "match_report_filename": None
    }
    log = pipeline_jobs[job_id]["log"]

    try:
        result = wbiops.run_wbi_pipeline(log)
        opps_df, matchmaking_df = result if result else (pd.DataFrame(), pd.DataFrame())

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

        log.append({"text": "🎉 Run Complete!"})
        pipeline_jobs[job_id]["status"] = "completed"

    except Exception as e:
        logging.error(f"Pipeline job {job_id} failed: {e}")
        log.append({"text": f"❌ Critical Error: {e}"})
        pipeline_jobs[job_id]["status"] = "failed"


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

# In app.py

def get_ai_boe_estimate(scope, personnel, case_history):
    """
    Calls the deployed DeepSeek model with a specialized prompt and case history.
    """
    endpoint = os.getenv("DEEPSEEK_AZURE_ENDPOINT")
    api_key = os.getenv("DEEPSEEK_AZURE_KEY")
    model_name = "WBI-Dash-DeepSeek" 

    if not endpoint or not api_key:
        logging.error("DEEPSEEK environment variables are missing.")
        return {"error": "AI service not configured."}

    # --- THIS IS THE CORRECTED PROMPT ---
    system_prompt = f"""
You are a specialist in finance and Basis of Estimate (BOE) development for government proposals, specifically for the Department of the Air Force. Your role is to create accurate, auditable, and competitive cost proposals. You must heavily rely on the provided Case History to inform your cost estimates. The current date is {datetime.now().strftime('%A, %B %d, %Y')}.

IMPORTANT: Your entire response must be ONLY the valid JSON object requested. Do not include any explanatory text, reasoning, or markdown formatting like ```json before or after the JSON object.
"""
    
    user_prompt = f"""
**Case History:**
{case_history if case_history else 'No case history provided.'}
---
**New Request:**

**Scope of Work:**
{scope}

**Available Personnel Roles:**
{', '.join(personnel)}
---
**Your Task:**
Generate the complete JSON Basis of Estimate based on the new request, using the case history as your primary guide. The JSON object must have keys: "work_plan", "materials_and_tools", "travel", and "subcontracts". Provide ONLY the JSON object.
"""

    try:
        from azure.ai.inference import ChatCompletionsClient
        from azure.ai.inference.models import SystemMessage, UserMessage
        from azure.core.credentials import AzureKeyCredential

        client = ChatCompletionsClient(
            endpoint=str(endpoint),
            credential=AzureKeyCredential(str(api_key)),
            api_version="2024-05-01-preview"
        )
        
        response = client.complete(
            model=model_name,
            messages=[
                SystemMessage(content=system_prompt),
                UserMessage(content=user_prompt)
            ],
            temperature=0.1,
            max_tokens=4096
        )
        
        if response.choices and response.choices[0].message and response.choices[0].message.content:
            raw_content = response.choices[0].message.content
            
            # Use a robust regex to find the JSON block
            match = re.search(r'\{.*\}', raw_content, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError as e:
                    logging.error(f"DeepSeek BoE Error: Failed to decode extracted JSON. Error: {e}. Extracted string: {match.group(0)}")
                    return {"error": "AI returned a malformed JSON object."}
            else:
                logging.error(f"DeepSeek BoE Error: No valid JSON object found in response: {raw_content}")
                return {"error": "AI returned a non-JSON response."}

        return {"error": "AI returned an empty response."}
    except Exception as e:
        logging.error(f"DeepSeek BoE Error: {e}", exc_info=True)
        return {"error": f"An error occurred with the AI service: {e}"}

@app.route("/status")
def status():
    return "App is running"

def get_improved_ai_summary(description, update):
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    key = os.getenv("AZURE_OPENAI_KEY")

    if not endpoint or not key or not update:
        logging.error("Azure OpenAI endpoint/key missing or no update provided.")
        return "Azure OpenAI not configured or no update provided."

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

    async def call_azure_openai():
        try:
            from openai import AsyncAzureOpenAI

            client = AsyncAzureOpenAI(
                azure_endpoint=str(endpoint),
                api_key=str(key),
                api_version="2024-02-01"
            )

            response = await client.chat.completions.create(
                model="gpt-4",
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

    try:
        return asyncio.run(call_azure_openai())
    except Exception as e:
        logging.error(f"Error running AI summary: {e}")
        return f"Error running AI summary: {e}"

@app.route('/api/run-pipeline', methods=['POST'])
def api_run_pipeline():
    """
    Starts the pipeline in a background thread and returns a job ID.
    """
    job_id = str(uuid.uuid4())
    thread = threading.Thread(target=run_pipeline_logic, args=(job_id,))
    thread.start()
    return jsonify({"job_id": job_id})

@app.route('/api/pipeline-status/<job_id>', methods=['GET'])
def get_pipeline_status(job_id):
    """
    Returns the status of a background pipeline job.
    """
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
    except Exception:
        return jsonify({"error": "Internal server error"}), 500

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

    ai_summary = get_improved_ai_summary(data.get('description', ''), data['managerUpdate'])
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
def api_estimate_boe():
    data = request.json
    if not data:
        return jsonify({"error": "Invalid request"}), 400
    
    scope = data.get('originalPrompt')
    personnel = data.get('personnel')
    if not scope or not personnel:
        return jsonify({"error": "Missing scope or personnel"}), 400
        
    case_history = ""
    try:
        history_path = os.path.join(basedir, 'tools', 'wbi_dataset_final.jsonl')
        with open(history_path, 'r') as f:
            first_line = f.readline()
            if first_line:
                entry = json.loads(first_line)
                case_history += entry['contents'][0]['parts'][0]['text'] + "\n"
                case_history += "RESULT:\n" + entry['contents'][1]['parts'][0]['text'] + "\n---\n"
    except Exception as e:
        logging.warning(f"Could not load case history: {e}")
    
    response_data = get_ai_boe_estimate(scope, personnel, case_history)
    
    if "error" in response_data:
        logging.error(f"AI estimation failed with error: {response_data['error']}")
        return jsonify(response_data), 500
        
    return jsonify(response_data)

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

def create_formatted_boe_excel(project_data, totals):
    """
    Creates a multi-sheet, formatted BoE Excel file in memory.
    """
    output_stream = io.BytesIO()
    with pd.ExcelWriter(output_stream, engine='xlsxwriter', engine_kwargs={"options": {}}) as writer:
        workbook = cast(Workbook, writer.book)
        
        # --- Define Formats ---
        title_format = workbook.add_format({'bold': True, 'font_size': 14, 'align': 'left'})
        currency_format = workbook.add_format({'num_format': '$#,##0.00'})
        total_currency_format = workbook.add_format({'num_format': '$#,##0.00', 'bold': True, 'top': 1, 'bottom': 1})
        
        # --- Cost Summary Sheet ---
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

        # --- Labor Detail Sheet ---
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

        # --- Other Detail Sheets ---
        if project_data.get('materials_and_tools'):
            pd.DataFrame(project_data['materials_and_tools']).to_excel(writer, sheet_name='Materials & Tools', index=False)
        if project_data.get('travel'):
            pd.DataFrame(project_data['travel']).to_excel(writer, sheet_name='Travel', index=False)
        if project_data.get('subcontracts'):
            pd.DataFrame(project_data['subcontracts']).to_excel(writer, sheet_name='Subcontracts', index=False)
            
    output_stream.seek(0)
    return output_stream

if __name__ == '__main__':
    app.run(debug=True, port=5001, host='0.0.0.0')
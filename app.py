import os
import pandas as pd
from datetime import datetime
from flask import Flask, render_template, jsonify, request, send_from_directory
import io
import json
import asyncio
import logging
import threading
import uuid

from tools import org_chart_parser
from tools import wbiops

from azure.storage.blob import BlobServiceClient
from azure.ai.inference.aio import ChatCompletionsClient
from azure.ai.inference.models import SystemMessage, UserMessage
from azure.core.credentials import AzureKeyCredential

# Ensure environment variables for Azure services are set in your deployment configuration.
# e.g., AZURE_STORAGE_CONNECTION_STRING, AZURE_AI_ENDPOINT, AZURE_AI_KEY
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

# A dictionary to store the status of background pipeline jobs
pipeline_jobs = {}

def run_pipeline_logic(job_id):
    """
    This function runs the WBI pipeline in a background thread.
    It updates the global `pipeline_jobs` dictionary with its status.
    """
    # Initialize job status
    pipeline_jobs[job_id] = {
        "status": "running",
        "log": [{"text": "✅ Pipeline Started"}],
        "opps_report_filename": None,
        "match_report_filename": None
    }
    log = pipeline_jobs[job_id]["log"]

    try:
        # Pass the log list to the pipeline function to append real-time updates
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

def get_ai_boe_estimate(scope, personnel):
    """
    Calls Azure OpenAI with improved prompting to get a structured BoE estimate.
    """
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    key = os.getenv("AZURE_OPENAI_KEY")

    if not endpoint or not key:
        logging.error("Azure OpenAI environment variables for BoE are missing.")
        return json.dumps({"error": "Azure AI service not configured."})

    examples = ""
    try:
        examples_path = os.path.join(basedir, 'tools', 'wbi_dataset_final.jsonl')
        with open(examples_path, 'r') as f:
            for i, line in enumerate(f):
                if i >= 2: 
                    break
                data = json.loads(line)
                user_text = data['contents'][0]['parts'][0]['text']
                model_text = data['contents'][1]['parts'][0]['text']
                examples += f"EXAMPLE INPUT:\n{user_text}\nEXAMPLE OUTPUT:\n{model_text}\n\n"
    except Exception as e:
        logging.error(f"Could not load few-shot examples: {e}")
    
    system_prompt = f"""
You are an expert project estimator for a defense contractor named WBI. Your task is to take a scope of work and generate a detailed Basis of Estimate (BoE) in a structured JSON format. You will be given examples of previous estimates to learn from.

You must provide a JSON object with the following keys: "work_plan", "materials_and_tools", "travel", and "subcontracts".

- "work_plan" must be a list of tasks. Each task object must have a "task" (string) and "hours" (object). The "hours" object must contain keys for each provided personnel role, with the estimated hours (integer).
- "materials_and_tools" must be a list of items. Each item must have "part_number", "description", "vendor", "quantity", and "unit_cost".
- "travel" must be a list of trips. Each trip must have "purpose", "trips", "travelers", "days", "airfare", "lodging", and "per_diem".
- "subcontracts" must be a list of subcontractors. Each must have "subcontractor", "description", and "cost".

If a category has no items, return an empty list for that key. Respond ONLY with the valid JSON object.
"""
    
    user_prompt = f"""
Here are some examples of how to perform the estimate:
{examples}
---
Now, perform the estimation for the following new request:

**Scope of Work:**
{scope}

**Available Personnel Roles:**
{', '.join(personnel)}
"""

    async def call_azure_openai_for_boe():
        try:
            from openai import AsyncAzureOpenAI
            client = AsyncAzureOpenAI(
                azure_endpoint=str(endpoint),
                api_key=str(key),
                api_version="2024-02-01",
                timeout=120.0
            )
            response = await client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.2,
                max_tokens=4096,
                response_format={"type": "json_object"}
            )
            if response.choices and response.choices[0].message and response.choices[0].message.content:
                return response.choices[0].message.content
            return json.dumps({"error": "AI returned an empty response."})
        except Exception as e:
            logging.error(f"Azure OpenAI BoE Error: {e}")
            return json.dumps({"error": f"An error occurred with the AI service: {e}"})

    return asyncio.run(call_azure_openai_for_boe())

@app.route("/status")
def status():
    return "App is running"

def get_improved_ai_summary(description, update):
    
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT") 
    key = os.getenv("AZURE_OPENAI_KEY")           

    if not endpoint or not key or not update:
        logging.error("Azure OpenAI endpoint/key missing or no update provided.")
        return "Azure OpenAI not configured or no update provided."

    system_prompt = "You are an expert Project Manager at WBI. Summarize the following project update."
    user_prompt = f"**Project Description:**\n{description}\n\n**Monthly Update:**\n{update}"

    async def call_azure_openai():
        try:
            
            from openai import AsyncAzureOpenAI

            client = AsyncAzureOpenAI(
                azure_endpoint=endpoint,
                api_key=key,
                api_version="2025-01-01-preview"  
            )

            response = await client.chat.completions.create(
                model="gpt-4",  
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]
            )
            
            message_content = response.choices[0].message.content
            if message_content:
                return message_content.strip()
            
            return ""

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
        return jsonify({"error": "Invalid request: No JSON body received."}), 400

    scope = data.get('originalPrompt')
    personnel = data.get('personnel')

    if not scope or not personnel:
        return jsonify({"error": "Missing scope or personnel data."}), 400
    
    ai_response_json = get_ai_boe_estimate(scope, personnel)
    
    try:
        response_data = json.loads(ai_response_json)
        return jsonify(response_data)
    except json.JSONDecodeError:
        return jsonify({"error": "Failed to decode AI response."}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5001, host='0.0.0.0')
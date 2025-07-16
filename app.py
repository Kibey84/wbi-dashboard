import os
import pandas as pd
from datetime import datetime
from flask import Flask, render_template, jsonify, request, send_from_directory
import io
import json
from dotenv import load_dotenv
import asyncio
import logging

from tools import org_chart_parser
from tools import wbiops
from tools import SBIR

from azure.storage.blob import BlobServiceClient
from azure.ai.inference.aio import ChatCompletionsClient
from azure.ai.inference.models import SystemMessage, UserMessage
from azure.core.credentials import AzureKeyCredential

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

def run_pipeline_logic():
    log = []
    log.append({"text": "✅ Pipeline Started"})
    opps_filename, match_filename = None, None
    try:
        result = wbiops.run_wbi_pipeline(log)
        if result:
            opps_df, matchmaking_df = result
        else:
            opps_df, matchmaking_df = pd.DataFrame(), pd.DataFrame()

        if opps_df is not None and not opps_df.empty:
            opps_filename = f"Opportunity_Report_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.xlsx"
            opps_df.to_excel(os.path.join(REPORTS_DIR, opps_filename), index=False)
            log.append({"text": f"📊 Primary Report Generated: {opps_filename}"})

        if matchmaking_df is not None and not matchmaking_df.empty:
            match_filename = f"Strategic_Matchmaking_Report_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.xlsx"
            matchmaking_df.to_excel(os.path.join(REPORTS_DIR, match_filename), index=False)
            log.append({"text": f"🤝 Matchmaking Report Generated: {match_filename}"})
    except Exception as e:
        log.append({"text": f"❌ A critical error occurred in the pipeline: {e}"})
    log.append({"text": "🎉 Run Complete!"})
    return {"log": log, "opps_report_filename": opps_filename, "match_report_filename": match_filename}

def get_unique_pms():
    df, error = load_project_data()
    if error or df.empty:
        return []
    return sorted(df['pm'].dropna().unique().tolist()) if 'pm' in df.columns else []

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
        column_mapping = {'project_id': 'projectName', 'project_pia': 'pi', 'project_owner': 'pm', 'project_date_started': 'startDate', 'project_date_completed': 'endDate', 'Status': 'status', 'project_description': 'description'}
        required_columns = list(column_mapping.keys())
        missing_cols = [col for col in required_columns if col not in df.columns]
        if missing_cols:
            return pd.DataFrame(), f"Missing columns: {missing_cols}"
        df_filtered = df[required_columns].copy().rename(columns=column_mapping)
        df_filtered['startDate'] = pd.to_datetime(df_filtered['startDate'], errors='coerce').dt.strftime('%Y-%m-%d')
        df_filtered['endDate'] = pd.to_datetime(df_filtered['endDate'], errors='coerce').dt.strftime('%Y-%m-%d')
        df_filtered.fillna('', inplace=True)
        return df_filtered, None
    except Exception as e:
        return pd.DataFrame(), str(e)

def get_improved_ai_summary(description, update):
    endpoint = os.getenv("AZURE_AI_ENDPOINT")
    key = os.getenv("AZURE_AI_KEY")
    if not endpoint or not key or not update:
        logging.error("Azure AI not configured or no update provided.")
        return "Azure AI not configured or no update provided."

    system_prompt = "You are an expert Project Manager at WBI..."
    user_prompt = f"**Project Description:**\n{description}\n\n**Monthly Update:**\n{update}"

    async def call_azure_ai(valid_endpoint, valid_key):
        try:
            client = ChatCompletionsClient(endpoint=valid_endpoint, credential=AzureKeyCredential(valid_key))
            response = await client.complete(deployment_name="gpt-4", messages=[SystemMessage(content=system_prompt), UserMessage(content=user_prompt)])
            return response.choices[0].message.content.strip()
        except Exception as e:
            logging.error(f"Error with Azure AI: {e}")
            return None

    try:
        return asyncio.run(call_azure_ai(endpoint, key))
    except Exception as e:
        return f"Error with Azure AI: {e}"

@app.route('/api/run-pipeline', methods=['POST'])
def api_run_pipeline():
    return jsonify(run_pipeline_logic())

@app.route('/api/parse-org-chart', methods=['POST'])
def api_parse_org_chart():
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
    try:
        output_filename = org_chart_parser.process_uploaded_pdf(file, REPORTS_DIR)
        if output_filename:
            return jsonify({"success": True, "filename": output_filename})
        return jsonify({"error": "Processing failed."}), 500
    except Exception as e:
        return jsonify({"error": "An internal error occurred."}), 500

@app.route('/api/pms')
def api_get_pms():
    return jsonify(get_unique_pms())

@app.route('/api/projects')
def api_get_projects():
    pm_name = request.args.get('pm')
    if not pm_name:
        return jsonify([])
    projects_df, error = load_project_data()
    if error:
        return jsonify([])
    pm_projects_df = projects_df[projects_df['pm'] == pm_name]
    return jsonify(pm_projects_df.to_dict(orient='records'))

@app.route('/api/get_update')
def api_get_update():
    project_name = request.args.get('projectName')
    month = request.args.get('month')
    year_str = request.args.get('year')

    if project_name is None or month is None or year_str is None:
        return jsonify({})

    try:
        if not str(year_str).isdigit():
            return jsonify({})
        year = int(year_str)
    except Exception:
        return jsonify({})

    connect_str = os.getenv('AZURE_STORAGE_CONNECTION_STRING')
    if not connect_str:
        logging.error("AZURE_STORAGE_CONNECTION_STRING is missing.")
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

        update = updates_df[(updates_df['projectName'] == project_name) & (updates_df['month'] == month) & (updates_df['year'] == year)]

        return jsonify(update.iloc[0].to_dict()) if not update.empty else jsonify({})
    except Exception as e:
        logging.error(f"Error getting update from Azure: {e}")
        return jsonify({})

@app.route('/api/update_project', methods=['POST'])
def api_update_project():
    data = request.json
    if not data:
        return jsonify({"success": False, "error": "Invalid request"}), 400
    project_name = data.get('projectName')
    month = data.get('month')
    year_str = data.get('year')
    manager_update = data.get('managerUpdate')
    description = data.get('description', '')
    if not all([project_name, month, year_str, manager_update]) or not str(year_str).isdigit():
        return jsonify({"success": False, "error": "Missing or invalid fields."}), 400
    try:
        year = int(year_str)
    except Exception:
        return jsonify({"success": False, "error": "Invalid year format."}), 400

    ai_summary = get_improved_ai_summary(description, manager_update)

    new_update_df = pd.DataFrame([{'projectName': project_name, 'month': month, 'year': year, 'managerUpdate': manager_update, 'aiSummary': ai_summary}])

    connect_str = os.getenv('AZURE_STORAGE_CONNECTION_STRING')
    if not connect_str:
        logging.error("AZURE_STORAGE_CONNECTION_STRING is missing.")
        return jsonify({"success": False, "error": "Storage connection string missing."}), 500

    try:
        blob_service_client = BlobServiceClient.from_connection_string(connect_str)
        blob_client = blob_service_client.get_blob_client(BLOB_CONTAINER_NAME, UPDATES_FILE)
        if blob_client.exists():
            with io.BytesIO() as stream:
                blob_client.download_blob().readinto(stream)
                stream.seek(0)
                updates_df = pd.read_csv(stream)
            condition = ~((updates_df['projectName'] == project_name) & (updates_df['month'] == month) & (updates_df['year'] == year))
            final_df = pd.concat([updates_df[condition], new_update_df], ignore_index=True)
        else:
            final_df = new_update_df
        blob_client.upload_blob(final_df.to_csv(index=False), overwrite=True)
        return jsonify({"success": True, "aiSummary": ai_summary})
    except Exception as e:
        logging.error(f"Error saving update to Azure: {e}")
        return jsonify({"success": False, "error": "Failed to save update."}), 500

@app.route('/download/<path:filename>')
def download_file(filename):
    return send_from_directory(REPORTS_DIR, filename, as_attachment=True)

@app.route('/')
def index():
    return render_template('index.html')

if __name__ == '__main__':
    app.run(debug=True, port=5001, host='0.0.0.0')

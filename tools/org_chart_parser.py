import os
import pandas as pd
import fitz  # PyMuPDF
import json
import re
from dotenv import load_dotenv
from openpyxl import load_workbook
from openpyxl.styles import Border, Side, Font, PatternFill
from openpyxl.utils import get_column_letter
from azure.ai.inference.aio import ChatCompletionsClient
from azure.ai.inference.models import SystemMessage, UserMessage
from azure.core.credentials import AzureKeyCredential
import asyncio

# --- CONFIGURATION ---
load_dotenv("samgovkey.env")
AZURE_AI_ENDPOINT = os.getenv("AZURE_AI_ENDPOINT")
AZURE_AI_KEY = os.getenv("ORG_CHART_API_KEY")
AZURE_AI_MODEL_NAME = "gpt-4"

def parse_with_ai(text_chunk):
    if not all([AZURE_AI_ENDPOINT, AZURE_AI_KEY]):
        print("Azure AI credentials for Org Chart Parser not configured.")
        return pd.DataFrame()

    prompt = f"""
    You are an expert data entry assistant. Parse the following text from an organizational chart and structure it into a clean JSON format.
    Rules:
    - The top-level object must have keys: "name", "leader", "title", "location".
    - It must have a "sub_units" key, containing a list of objects. Each sub-unit object should also have "name", "leader", "title", and "location" keys.
    - If information is missing, return an empty string for that key.
    - Provide only the JSON object as your response.
    ---
    TEXT TO PARSE:
    {text_chunk}
    """
    
    async def call_azure_ai(endpoint_str, key_str):
        client = ChatCompletionsClient(endpoint=endpoint_str, credential=AzureKeyCredential(key_str))
        response = await client.complete(
            deployment_name=AZURE_AI_MODEL_NAME,
            messages=[UserMessage(content=prompt)],
            max_tokens=2048, temperature=0.2
        )
        return response.choices[0].message.content.strip()
    
    try:
        ai_response_text = asyncio.run(call_azure_ai(AZURE_AI_ENDPOINT, AZURE_AI_KEY))
        match = re.search(r'\{.*\}', ai_response_text, re.DOTALL)
        if not match: raise ValueError("No valid JSON object in AI response.")
        data = json.loads(match.group(0))
        return pd.DataFrame([data])
    except Exception as e:
        print(f"Error in org_chart_parser.parse_with_ai: {e}")
        return pd.DataFrame()

def save_and_format_excel(df, output_directory, output_filename):
    if df.empty: return
    full_path = os.path.join(output_directory, output_filename)
    df.to_excel(full_path, index=False)
    print(f"Formatted Excel saved to {full_path}")

def process_uploaded_pdf(uploaded_file, output_directory):
    try:
        pdf_bytes = uploaded_file.read()
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            full_text = "".join(page.get_text("text") for page in doc) # type: ignore  
                    
        ai_df = parse_with_ai(full_text)
        
        if not ai_df.empty:
            original_filename = os.path.splitext(uploaded_file.filename)[0]
            output_filename = f"AI_Formatted_{original_filename}.xlsx"
            save_and_format_excel(ai_df, output_directory, output_filename)
            return output_filename
        return None
    except Exception as e:
        print(f"An error occurred in process_uploaded_pdf: {e}")
        return None
import os
import pandas as pd
import fitz  # PyMuPDF
import json
import re
from dotenv import load_dotenv
from azure.ai.inference.aio import ChatCompletionsClient
from azure.ai.inference.models import SystemMessage, UserMessage
from azure.core.credentials import AzureKeyCredential
import asyncio

# === Load Environment Variables ===
load_dotenv("samgovkey.env")

AZURE_AI_ENDPOINT = os.getenv("AZURE_AI_ENDPOINT")
AZURE_AI_KEY = os.getenv("AZURE_AI_KEY")
AZURE_AI_MODEL_NAME = os.getenv("AZURE_AI_MODEL_NAME")

# === Azure OpenAI Call ===
async def call_azure_ai(prompt_text: str) -> str | None:
    if not AZURE_AI_ENDPOINT or not AZURE_AI_KEY or not AZURE_AI_MODEL_NAME:
        print("[Config Error] Azure AI credentials or model name not configured.")
        return None

    try:
        client = ChatCompletionsClient(
            endpoint=str(AZURE_AI_ENDPOINT),
            credential=AzureKeyCredential(str(AZURE_AI_KEY))
        )
        response = await client.complete(
            deployment_name=str(AZURE_AI_MODEL_NAME),
            messages=[
                SystemMessage(content="You are an expert data entry assistant for org charts."),
                UserMessage(content=prompt_text)
            ],
            max_tokens=2048,
            temperature=0.2
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"[Azure AI Error] {e}")
        return None

# === Org Chart Parsing Logic ===
def parse_with_ai(text_chunk: str) -> pd.DataFrame:
    if not AZURE_AI_ENDPOINT or not AZURE_AI_KEY or not AZURE_AI_MODEL_NAME:
        print("[Config Error] Azure AI credentials or model name not configured.")
        return pd.DataFrame()

    prompt = f"""
You are an expert data entry assistant. Parse the following text from an organizational chart and structure it into a clean JSON format.

Rules:
- The top-level object must have keys: "name", "leader", "title", "location".
- It must have a "sub_units" key, containing a list of objects. Each sub-unit object should also have "name", "leader", "title", and "location" keys.
- If information is missing, return an empty string for that key.
- Provide ONLY the JSON object as your response.

---
TEXT TO PARSE:
{text_chunk}
"""

    try:
        ai_response_text = asyncio.run(call_azure_ai(prompt))
        if not ai_response_text:
            print("[AI Error] No response received.")
            return pd.DataFrame()

        match = re.search(r'\{.*\}', ai_response_text, re.DOTALL)
        if not match:
            print("[AI Error] No valid JSON object found in response.")
            return pd.DataFrame()

        data = json.loads(match.group(0))
        return pd.DataFrame([data])

    except Exception as e:
        print(f"[Parser Error] {e}")
        return pd.DataFrame()

# === Excel Output ===
def save_and_format_excel(df: pd.DataFrame, output_directory: str, output_filename: str) -> None:
    if df.empty:
        print("[Excel Output] DataFrame is empty. Skipping save.")
        return
    os.makedirs(output_directory, exist_ok=True)
    full_path = os.path.join(output_directory, output_filename)
    df.to_excel(full_path, index=False)
    print(f"[Excel Output] Saved file at {full_path}")

# === PDF Processing ===
def process_uploaded_pdf(uploaded_file, output_directory: str) -> str | None:
    try:
        pdf_bytes = uploaded_file.read()
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            full_text = "".join(page.get_text() for page in doc)  #type: ignore
            
        ai_df = parse_with_ai(full_text)
        if ai_df.empty:
            print("[PDF Processing] AI returned no data.")
            return None

        original_filename = os.path.splitext(uploaded_file.filename)[0]
        output_filename = f"AI_Formatted_{original_filename}.xlsx"
        save_and_format_excel(ai_df, output_directory, output_filename)
        return output_filename

    except Exception as e:
        print(f"[PDF Processing Error] {e}")
        return None

import os
import pandas as pd
import fitz  # PyMuPDF
import json
import re
import asyncio
from typing import Optional, List

from openai import AsyncAzureOpenAI

# === Azure OpenAI Call ===
async def call_azure_ai(prompt_text: str) -> Optional[str]:
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    key = os.getenv("AZURE_OPENAI_KEY")

    if not endpoint or not key:
        print("[Config Error] Azure OpenAI credentials not configured.")
        return None

    try:
        client = AsyncAzureOpenAI(
            azure_endpoint=endpoint,
            api_key=key,
            api_version="2025-01-01-preview",
            timeout=90.0  
        )

        response = await client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are an expert data entry assistant for org charts."},
                {"role": "user", "content": prompt_text}
            ],
            max_tokens=4096,
            temperature=0.1
        )
        
        if response.choices and response.choices[0].message and response.choices[0].message.content:
            return response.choices[0].message.content.strip()
        return ""

    except Exception as e:
        print(f"[Azure AI Error] {e}")
        return None

# === Org Chart Parsing Logic ===
def parse_text_chunk(text_chunk: str) -> List[dict]:
    prompt = f"""
    You are an expert data entry assistant. From the text below, extract a list of all distinct organizational units.
    
    Rules:
    - Create a JSON list where each object has the keys "name", "leader", "title", and "location".
    - If a piece of information is missing for a unit, use an empty string.
    - Focus only on the text provided. Do not invent or infer information.
    - Provide ONLY the JSON list as your response, starting with [ and ending with ].

    ---
    TEXT TO PARSE:
    {text_chunk}
    """
    try:
        ai_response_text = asyncio.run(call_azure_ai(prompt))

        if not ai_response_text:
            print("[AI Error] No response received for chunk.")
            return []

        match = re.search(r'\[.*\]', ai_response_text, re.DOTALL)
        if not match:
            print("[AI Error] No valid JSON list found in response.")
            return []
            
        json_string = match.group(0)
        return json.loads(json_string)

    except Exception as e:
        print(f"[Parser Error] {e}")
        return []

# === Excel Output ===
def save_and_format_excel(df: pd.DataFrame, output_directory: str, output_filename: str) -> None:
    if df.empty:
        print("[Excel Output] DataFrame is empty. Skipping save.")
        return
    os.makedirs(output_directory, exist_ok=True)
    full_path = os.path.join(output_directory, output_filename)
    
    with pd.ExcelWriter(full_path, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='OrgChart')
        worksheet = writer.sheets['OrgChart']
        for column in worksheet.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = (max_length + 2)
            worksheet.column_dimensions[column_letter].width = adjusted_width
    print(f"[Excel Output] Saved formatted file at {full_path}")

# === PDF Processing ===
def process_uploaded_pdf(uploaded_file, output_directory: str) -> Optional[str]:
    try:
        if not hasattr(uploaded_file, 'filename') or not uploaded_file.filename:
            print("[File Error] Uploaded file has no filename.")
            return None
            
        all_units = []
        pdf_bytes = uploaded_file.read()
        
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            print(f"[PDF Processing] Starting to process {len(doc)} pages.")
            for i, page in enumerate(doc): # type: ignore
                print(f"[PDF Processing] Analyzing page {i+1}...")
                page_text = page.get_text("text")
                if page_text.strip():
                    units_from_page = parse_text_chunk(page_text)
                    if units_from_page:
                        all_units.extend(units_from_page)
                else:
                    print(f"[PDF Processing] Page {i+1} has no text.")
        
        if not all_units:
            print("[PDF Processing] AI returned no data from any page.")
            return None

        df = pd.DataFrame(all_units)
        df = df.rename(columns={"name": "Unit", "leader": "Leader", "title": "Title", "location": "Location"})

        original_filename = os.path.splitext(uploaded_file.filename)[0]
        output_filename = f"AI_Formatted_{original_filename}.xlsx"
        save_and_format_excel(df, output_directory, output_filename)
        return output_filename

    except Exception as e:
        print(f"[PDF Processing Error] {e}")
        return None
import os
import pandas as pd
import fitz  # PyMuPDF
import json
import re
import asyncio
from typing import Optional

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
            api_version="2025-01-01-preview" 
        )

        response = await client.chat.completions.create(
            model="gpt-4",  
            messages=[
                {"role": "system", "content": "You are an expert data entry assistant for org charts."},
                {"role": "user", "content": prompt_text}
            ],
            max_tokens=2048,
            temperature=0.2
        )
        
        if response.choices and response.choices[0].message and response.choices[0].message.content:
            return response.choices[0].message.content.strip()
        return ""

    except Exception as e:
        print(f"[Azure AI Error] {e}")
        return None

# === Org Chart Parsing Logic ===
def parse_with_ai(text_chunk: str) -> pd.DataFrame:
    prompt = f"""
You are an expert data entry assistant. Parse the following text from an organizational chart and structure it into a clean JSON format.

Rules:
- The top-level object must have a "name" key for the main organization.
- It must have a "sub_units" key, containing a list of objects. Each sub-unit object must have a "name" key, and can optionally have "leader", "title", and "location" keys.
- If information is missing, return an empty string for that key.
- Provide ONLY the JSON object as your response, starting with {{ and ending with }}.

---
TEXT TO PARSE:
{text_chunk}
"""
    try:
        ai_response_text = asyncio.run(call_azure_ai(prompt))

        if not ai_response_text:
            print("[AI Error] No response received.")
            return pd.DataFrame()

        print(f"[AI Debug] Raw response from AI: {ai_response_text}") 

        match = re.search(r'\{.*\}', ai_response_text, re.DOTALL)
        if not match:
            print("[AI Error] No valid JSON object found in response.")
            return pd.DataFrame()

        json_string = match.group(0)
        data = json.loads(json_string)
        
        flat_data = []
        
        main_unit_name = data.get('name', 'Main Organization (Name not found)')
        flat_data.append({
            'Unit': main_unit_name,
            'Leader': data.get('leader', ''),
            'Title': data.get('title', ''),
            'Location': data.get('location', '')
        })

        if 'sub_units' in data and isinstance(data['sub_units'], list):
            for i, subunit in enumerate(data.get('sub_units', [])):
                prefix = '  ↳ ' if i < len(data['sub_units']) else '  └ '
                
                flat_data.append({
                    'Unit': f"{prefix}{subunit.get('name', 'Sub-unit (Name not found)')}",
                    'Leader': subunit.get('leader', ''),
                    'Title': subunit.get('title', ''),
                    'Location': subunit.get('location', '')
                })
        
        final_df = pd.DataFrame(flat_data)
        print(f"[AI Debug] Created DataFrame with {len(final_df)} rows.") 
        return final_df

    except json.JSONDecodeError as e:
        print(f"[Parser Error] Failed to decode JSON: {e}")
        print(f"[Parser Error] Malformed JSON string was: {json_string}")
        return pd.DataFrame()
    except Exception as e:
        print(f"[Parser Error] An unexpected error occurred: {e}")
        return pd.DataFrame()

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

# === PDF Processing  ===
def process_uploaded_pdf(uploaded_file, output_directory: str) -> Optional[str]:
    try:
        if not hasattr(uploaded_file, 'filename') or not uploaded_file.filename:
            print("[File Error] Uploaded file has no filename.")
            return None
            
        pdf_bytes = uploaded_file.read()
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            full_text = "".join(page.get_text() for page in doc) #type: ignore  
            if not full_text.strip():
                print("[PDF Processing] No text could be extracted from the PDF. It might be an image-only file.")
                return None
            
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
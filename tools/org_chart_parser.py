import os
import pandas as pd
import fitz  # PyMuPDF
import json
import re
import asyncio
from typing import Optional, List, Dict, Any

from openai import AsyncAzureOpenAI
from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet
from openpyxl.styles import PatternFill, Font, Alignment
from openpyxl.utils import get_column_letter

# === Azure OpenAI Call ===
async def call_azure_ai(client: AsyncAzureOpenAI, prompt_text: str) -> Optional[str]:
    """Asynchronously calls the Azure AI model using a shared client."""
    deployment_name = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    assert deployment_name is not None

    try:
        response = await client.chat.completions.create(
            model=deployment_name,
            messages=[
                {"role": "system", "content": "You are an expert data entry assistant for org charts."},
                {"role": "user", "content": prompt_text}
            ],
            max_tokens=4096,
            temperature=0.1,
            response_format={"type": "json_object"}
        )

        if response.choices and response.choices[0].message and response.choices[0].message.content:
            return response.choices[0].message.content.strip()
        return ""

    except Exception as e:
        print(f"[Azure AI Error] {e}")
        return None

# === Org Chart Parsing Logic ===
async def parse_text_chunk(client: AsyncAzureOpenAI, text_chunk: str) -> List[dict]:
    """Asynchronously parses a chunk of text using the AI model."""
    prompt = f"""
    You are an expert data entry assistant. From the text below, extract a list of all distinct organizational units.
    
    Rules:
    - Create a JSON list where each object has the keys "name", "leader", "title", and "location".
    - If a piece of information is missing for a unit, use an empty string "".
    - Focus only on the text provided. Do not invent or infer information.
    - Provide ONLY the JSON list as your response.

    ---
    TEXT TO PARSE:
    {text_chunk}
    """
    try:
        ai_response_text = await call_azure_ai(client, prompt)

        if not ai_response_text:
            print("[AI Error] No response received for chunk.")
            return []

        return json.loads(ai_response_text)

    except json.JSONDecodeError:
        print("[AI Error] Failed to decode JSON from AI response.")
        return []
    except Exception as e:
        print(f"[Parser Error] {e}")
        return []

# === Excel Output ===
def save_and_format_excel(df: pd.DataFrame, output_directory: str, output_filename: str) -> None:
    """Saves the DataFrame to a formatted Excel file, grouped by location."""
    if df.empty:
        print("[Excel Output] DataFrame is empty. Skipping save.")
        return
    os.makedirs(output_directory, exist_ok=True)
    full_path = os.path.join(output_directory, output_filename)
    
    df['Location'] = df['Location'].fillna('Unknown Location')
    df_sorted = df.sort_values(by='Location')

    wb = Workbook()
    ws = wb.active
    assert isinstance(ws, Worksheet)
    ws.title = "OrgChart"

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="4F81BD", end_color="4F81BD", fill_type="solid")
    location_font = Font(bold=True, color="FFFFFF")
    location_fill = PatternFill(start_color="808080", end_color="808080", fill_type="solid")
    row_fill_1 = PatternFill(start_color="DCE6F1", end_color="DCE6F1", fill_type="solid")
    row_fill_2 = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")

    headers = ["Unit", "Leader", "Title", "Location"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center', vertical='center')

    current_location = None
    row_color_index = 0
    for index, row in df_sorted.iterrows():
        if row['Location'] != current_location:
            current_location = row['Location']
            ws.append([current_location] + [''] * (len(headers) - 1))
            location_header_row = ws.max_row
            ws.merge_cells(start_row=location_header_row, start_column=1, end_row=location_header_row, end_column=len(headers))
            cell = ws.cell(row=location_header_row, column=1)
            cell.font = location_font
            cell.fill = location_fill
            cell.alignment = Alignment(horizontal='center', vertical='center')
            row_color_index = 0

        data_row = [row.get(col, '') for col in ["Unit", "Leader", "Title", "Location"]]
        ws.append(data_row)
        
        current_fill = row_fill_1 if row_color_index % 2 == 0 else row_fill_2
        for cell in ws[ws.max_row]:
            cell.fill = current_fill
        row_color_index += 1

    for col_idx, column_cells in enumerate(ws.columns, 1):
        max_length = 0
        for cell in column_cells:
            try:
                if len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except:
                pass
        adjusted_width = (max_length + 2)
        ws.column_dimensions[get_column_letter(col_idx)].width = adjusted_width

    wb.save(full_path)
    print(f"[Excel Output] Saved formatted file at {full_path}")

# === PDF Processing ===
async def _async_process_pdf(pdf_bytes: bytes) -> List[dict]:
    """The core async logic for processing the PDF."""
    all_units = []
    
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    key = os.getenv("AZURE_OPENAI_KEY")
    if not endpoint or not key:
        print("[Config Error] Azure OpenAI credentials not found for async processing.")
        return []

    client = AsyncAzureOpenAI(
        azure_endpoint=endpoint,
        api_key=key,
        api_version="2024-02-01",
        timeout=90.0
    )

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        print(f"[PDF Processing] Starting to process {len(doc)} pages.")
        
        tasks = []
        for i, page in enumerate(doc): #type:ignore
            page_text = page.get_text("text")
            if page_text.strip():
                tasks.append(parse_text_chunk(client, page_text))
            else:
                print(f"[PDF Processing] Page {i+1} has no text.")
        
        page_results = await asyncio.gather(*tasks)
        
        for result_list in page_results:
            all_units.extend(result_list)
            
    return all_units

def process_uploaded_pdf(uploaded_file, output_directory: str) -> Optional[str]:
    """Synchronous wrapper that runs the entire async PDF processing workflow."""
    try:
        if not hasattr(uploaded_file, 'filename') or not uploaded_file.filename:
            print("[File Error] Uploaded file has no filename.")
            return None
            
        pdf_bytes = uploaded_file.read()
        
        all_units = asyncio.run(_async_process_pdf(pdf_bytes))
        
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

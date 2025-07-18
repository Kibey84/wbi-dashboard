# sbir_pipeline_scraper.py
import os
import logging
import docx
import pandas as pd

try:
    import sbir
    import phase2
    import grading
except ImportError as e:
    logging.error(f"SBIR Pipeline: Could not import a phase script. {e}")
    def fetch_sbir_partnership_opportunities():
        return []

def run_and_read_sbir_pipeline():
    logging.info("[SBIR PIPELINE] Running original 3-phase SBIR pipeline to generate Word docs...")
    sbir.run_phase_1()
    phase2.run_phase_2()
    grading.run_phase_3() # This creates the Word docs
    logging.info("[SBIR PIPELINE] Word document generation complete.")
    
    logging.info("[SBIR PIPELINE] Reading generated Word dossiers...")
    dossier_folder = 'dossiers' # Assumes Word docs are saved in this sub-folder
    if not os.path.exists(dossier_folder):
        logging.error(f"[SBIR PIPELINE] Dossier folder '{dossier_folder}' not found.")
        return []
    
    partner_list = []
    for filename in os.listdir(dossier_folder):
        if filename.endswith('.docx'):
            try:
                doc_path = os.path.join(dossier_folder, filename)
                doc = docx.Document(doc_path)
                doc_text = "\n".join([p.text for p in doc.paragraphs])
                company_name = "Unknown Company"
                project_title = filename.replace('.docx', '')
                for para in doc.paragraphs:
                    if para.text.strip().lower().startswith("company name:"):
                        company_name = para.text.split(":", 1)[1].strip()
                    if para.text.strip().lower().startswith("project title:"):
                        project_title = para.text.split(":", 1)[1].strip()
                partner_list.append({"company_name": company_name, "project_title": project_title, "full_text": doc_text})
            except Exception as e:
                logging.warning(f"Could not parse Word document '{filename}': {e}")
    logging.info(f"[SBIR PIPELINE] Successfully parsed {len(partner_list)} SBIR partner dossiers.")
    return partner_list

def fetch_sbir_partnership_opportunities():
    return run_and_read_sbir_pipeline()
from .dod_sbir_scraper import fetch_dod_sbir_sttr_topics
from .nasa_sbir_module import fetch_nasa_sbir_opportunities
from .darpa_module import fetch_darpa_opportunities
from .arpah_module import fetch_arpah_opportunities
from .eureka_module import fetch_eureka_opportunities
from .nsin_module import fetch_nsin_opportunities
from .nih_sbir_module import fetch_nih_sbir_opportunities
from .nstxl_module import fetch_nstxl_opportunities
from .mtec_module import fetch_mtec_opportunities
from .afwerx_module import fetch_afwerx_opportunities
from .diu_scraper import fetch_diu_opportunities
from .socom_baa_module import fetch_socom_opportunities
from .arl_opportunities_module import fetch_arl_opportunities
from .nasc_solutions_module import fetch_nasc_opportunities
from .osti_foa_module import fetch_osti_foas
from .arpae_scraper import fetch_arpae_opportunities
from .iarpa_scraper import fetch_iarpa_opportunities
from .sbir_pipeline_scraper import fetch_sbir_partnership_opportunities
from .sam_gov_module import fetch_sam_gov_opportunities

FETCH_FUNCTIONS = {
    "DoD SBIR/STTR": fetch_dod_sbir_sttr_topics,
    "NASA SBIR": fetch_nasa_sbir_opportunities,
    "DARPA": fetch_darpa_opportunities,
    "ARPA-H": fetch_arpah_opportunities,
    "EUREKA": fetch_eureka_opportunities,
    "NSIN": fetch_nsin_opportunities,
    "NIH SBIR": fetch_nih_sbir_opportunities,
    "NSTXL": fetch_nstxl_opportunities,
    "MTEC": fetch_mtec_opportunities,
    "AFWERX": fetch_afwerx_opportunities,
    "DIU": fetch_diu_opportunities,
    "SOCOM BAA": fetch_socom_opportunities,
    "ARL": fetch_arl_opportunities,
    "NASC Solutions": fetch_nasc_opportunities,
    "OSTI FOA": fetch_osti_foas,
    "ARPA-E": fetch_arpae_opportunities,
    "IARPA": fetch_iarpa_opportunities,
    "SBIR Partnerships": fetch_sbir_partnership_opportunities,
    "SAM.gov": fetch_sam_gov_opportunities
}

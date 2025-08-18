
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
    "fetch_dod_sbir_sttr_topics": fetch_dod_sbir_sttr_topics,
    "fetch_nasa_sbir_opportunities": fetch_nasa_sbir_opportunities,
    "fetch_darpa_opportunities": fetch_darpa_opportunities,
    "fetch_arpah_opportunities": fetch_arpah_opportunities,
    "fetch_eureka_opportunities": fetch_eureka_opportunities,
    "fetch_nsin_opportunities": fetch_nsin_opportunities,
    "fetch_nih_sbir_opportunities": fetch_nih_sbir_opportunities,
    "fetch_nstxl_opportunities": fetch_nstxl_opportunities,
    "fetch_mtec_opportunities": fetch_mtec_opportunities,
    "fetch_afwerx_opportunities": fetch_afwerx_opportunities,
    "fetch_diu_opportunities": fetch_diu_opportunities,
    "fetch_socom_opportunities": fetch_socom_opportunities,
    "fetch_arl_opportunities": fetch_arl_opportunities,
    "fetch_nasc_opportunities": fetch_nasc_opportunities,
    "fetch_osti_foas": fetch_osti_foas,
    "fetch_arpae_opportunities": fetch_arpae_opportunities,
    "fetch_iarpa_opportunities": fetch_iarpa_opportunities,
    "fetch_sbir_partnership_opportunities": fetch_sbir_partnership_opportunities,
    "fetch_sam_gov_opportunities": fetch_sam_gov_opportunities,
}

"""
All configuration in one place: env-driven settings, plus the two static
maps that encode source reliability. These maps are hand-authored, not
inferred by the LLM — deciding "which policy version is current" or "whose
contract this is" from document content would be exactly the guessing this
assessment is testing against.
"""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-20b"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    pdf_dir: str = str(BASE_DIR / "data" / "pdfs")
    workbook_path: str = str(BASE_DIR / "data" / "ParcelPilot_Assessment_Data.xlsx")
    chroma_persist_dir: str = str(BASE_DIR / "data" / "db" / "chroma")
    sqlite_db_path: str = str(BASE_DIR / "data" / "db" / "parcelpilot.db")
    checkpoint_db_path: str = str(BASE_DIR / "data" / "db" / "checkpoints.sqlite")

    # Fallback "now" for time-based questions if the workbook has no README
    # sheet / snapshot row. Overwritten by ingest.py from the real workbook.
    default_snapshot_time: str = "2026-08-20T09:00:00"

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore"
    )

settings = Settings()

# Lower rank = more authoritative. A customer's own contract outranks
# general policy; a deprecated policy is (near-)never authoritative.
DOC_AUTHORITY_RANK = {
    "contract": 0,
    "cancellation_sop": 1,
    "support_policy_current": 1,
    "product_ops": 1,
    "support_policy_deprecated": 99,
}

# Map filename -> metadata. Edit this if your real file names differ from
# the ones listed in the assessment brief.
DOCUMENT_METADATA = {
    "01_Support_Policy_v3_CURRENT.pdf": {
        "doc_type": "support_policy_current", "status": "current",
        "version": "v3", "customer_id": None,
    },
    "02_Support_Policy_v2_DEPRECATED.pdf": {
        "doc_type": "support_policy_deprecated", "status": "deprecated",
        "version": "v2", "customer_id": None,
    },
    "03_Cancellation_and_Service_Credit_SOP_v4.pdf": {
        "doc_type": "cancellation_sop", "status": "current",
        "version": "v4", "customer_id": None,
    },
    "04_Product_Operations_Guide_and_Known_Issues.pdf": {
        "doc_type": "product_ops", "status": "current",
        "version": "v1", "customer_id": None,
    },
    "05_Northstar_Logistics_Enterprise_Agreement.pdf": {
        "doc_type": "contract", "status": "current",
        "version": "v1", "customer_id": "ACCT-001",
    },
    "06_LumenWorks_Service_Agreement.pdf": {
        "doc_type": "contract", "status": "current",
        "version": "v1", "customer_id": "ACCT-002",
    },
}
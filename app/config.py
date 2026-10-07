import os
import secrets
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings:
    app_name = os.getenv("APP_NAME", "ระบบค้นหาข้อมูลพนักงาน")
    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://employee:employee@db:5432/employee_directory",
    )
    secret_key = os.getenv("SECRET_KEY") or secrets.token_urlsafe(48)
    upload_dir = Path(os.getenv("UPLOAD_DIR", str(BASE_DIR / "uploads")))
    max_upload_bytes = int(os.getenv("MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
    admin_username = os.getenv("ADMIN_USERNAME", "admin")
    admin_password = os.getenv("ADMIN_PASSWORD", "")
    session_https_only = os.getenv("SESSION_HTTPS_ONLY", "false").lower() == "true"


settings = Settings()

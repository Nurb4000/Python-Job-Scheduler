import os
from dotenv import load_dotenv

load_dotenv()


# Values that must never reach a running instance (sample/placeholder secrets).
WEAK_SECRET_KEYS = {
    '',
    'dev-secret-key',
    'change-this-to-a-random-secret-key',
}


class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', '')
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL', 'sqlite:///python_job_scheduler.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = True

    ADMIN_EMAIL = os.getenv('ADMIN_EMAIL', 'admin@example.com')
    ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', '')
    TIMEZONE = os.getenv('TIMEZONE', 'America/New_York')
    SMTP_SERVER = os.getenv('SMTP_SERVER', '')
    SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
    SMTP_USERNAME = os.getenv('SMTP_USERNAME', '')
    SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '')
    SMTP_FROM = os.getenv('SMTP_FROM', '')
    EMAIL_ENABLED = os.getenv('EMAIL_ENABLED', 'true').lower() == 'true'
    SCRIPT_TIMEOUT = int(os.getenv('SCRIPT_TIMEOUT', '300'))
    # 0 = keep JobRun history forever; otherwise rows older than N days are
    # pruned daily by the scheduler (and once at startup).
    JOB_RUN_RETENTION_DAYS = int(os.getenv('JOB_RUN_RETENTION_DAYS', '0'))
    # Failed logins allowed per IP+username before a temporary lockout;
    # 0 disables login rate limiting.
    LOGIN_MAX_ATTEMPTS = int(os.getenv('LOGIN_MAX_ATTEMPTS', '5'))
    LOGIN_WINDOW_SECONDS = int(os.getenv('LOGIN_WINDOW_SECONDS', '900'))

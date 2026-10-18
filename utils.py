import os
import subprocess
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

import pytz
from flask import current_app
from werkzeug.utils import secure_filename

MAX_SCRIPT_SIZE = 5 * 1024 * 1024
ALLOWED_SCRIPT_EXTENSIONS = {'.py'}


def validate_script_upload(script_file):
    """Validate an uploaded script.

    Returns (ok, filename_or_error). On success the second value is the
    secure_filename of the upload; on failure it is a user-facing message.
    """
    filename = secure_filename(script_file.filename or '')
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_SCRIPT_EXTENSIONS:
        return False, 'Script must be a .py file.'
    data = script_file.read()
    script_file.seek(0)
    if len(data) > MAX_SCRIPT_SIZE:
        return False, 'Script exceeds the 5 MB size limit.'
    if not filename:
        return False, 'Invalid script filename.'
    return True, filename


def get_tz():
    return pytz.timezone(current_app.config['TIMEZONE'])


def now_utc():
    return datetime.utcnow()


def now_tz():
    return datetime.now(get_tz())


def generate_job_number():
    from extensions import db
    from models import Schedule
    prefix = 'JOB'
    last = Schedule.query.order_by(Schedule.id.desc()).first()
    num = 1 if last is None else last.id + 1
    return f'{prefix}-{num:05d}'


def generate_run_number():
    from extensions import db
    from models import JobRun
    prefix = 'R'
    last = JobRun.query.order_by(JobRun.id.desc()).first()
    num = 1 if last is None else last.id + 1
    return f'{prefix}-{num:05d}'


def format_datetime(dt, fmt='%Y-%m-%d %I:%M %p'):
    if dt is None:
        return ''
    tz = get_tz()
    utc_dt = pytz.utc.localize(dt) if dt.tzinfo is None else dt
    local_dt = utc_dt.astimezone(tz)
    return local_dt.strftime(fmt)


def run_python_script(script_path):
    try:
        result = subprocess.run(
            ['python3', script_path],
            capture_output=True, text=True, timeout=300
        )
        output = ''
        if result.stdout:
            output += result.stdout
        if result.stderr:
            if output:
                output += '\n'
            output += result.stderr
        if result.returncode != 0:
            return False, result.stderr or 'Script failed', output
        return True, '', output
    except subprocess.TimeoutExpired:
        return False, 'Script timed out after 300 seconds', ''
    except Exception as e:
        return False, str(e), ''


def send_email(to_addr, subject, body):
    config = current_app.config
    if not config['EMAIL_ENABLED']:
        return True

    msg = MIMEMultipart()
    msg['From'] = config['SMTP_FROM']
    msg['To'] = to_addr
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    try:
        with smtplib.SMTP(config['SMTP_SERVER'], config['SMTP_PORT'], timeout=10) as server:
            if server.has_extn('STARTTLS'):
                server.starttls()
            username = config.get('SMTP_USERNAME', '')
            password = config.get('SMTP_PASSWORD', '')
            if username and password and server.has_extn('AUTH'):
                server.login(username, password)
            server.send_message(msg)
        return True
    except Exception as e:
        current_app.logger.error(f'Email send failed: {e}')
        return False

"""Verification tests for the High fixes (H1-H7) in Python-Job-Scheduler.

Run: python tests/test_high_fixes.py
(Do NOT run from inside the project folder — the real .env must not load.)
"""
import io
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta

# --- configure environment BEFORE importing the app ---
_TEST_TMP = tempfile.mkdtemp(prefix='pjs_test_')
TEST_DB = os.path.join(_TEST_TMP, 'test_pjs_high.db')
PROJECT = os.environ.get('PJS_PROJECT') or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(PROJECT, 'uploaded_scripts')

if os.path.exists(TEST_DB):
    os.remove(TEST_DB)

os.environ.update({
    'DATABASE_URL': f'sqlite:///{TEST_DB}',
    'SECRET_KEY': 'test-secret',
    'ADMIN_EMAIL': 'admin@test.local',
    'ADMIN_PASSWORD': 'testpass123',
    'TIMEZONE': 'UTC',
    'EMAIL_ENABLED': 'false',
    'FLASK_DEBUG': 'false',
})

sys.path.insert(0, PROJECT)

from app import create_app  # noqa: E402
from extensions import db  # noqa: E402
from models import User, Schedule, JobRun  # noqa: E402
from scheduler_service import build_cron_expression, run_schedule  # noqa: E402

PASS, FAIL = [], []
CREATED_FILES = []


def check(name, cond, detail=''):
    if cond:
        PASS.append(name)
        print(f'  PASS  {name}')
    else:
        FAIL.append(name)
        print(f'  FAIL  {name}  {detail}')


def get_csrf(client, path='/login'):
    r = client.get(path)
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', r.get_data(as_text=True))
    assert m, f'csrf token not found at {path}'
    return m.group(1)


def login(client):
    token = get_csrf(client)
    return client.post('/login', data={
        'username': 'admin', 'password': 'testpass123', 'csrf_token': token,
    }, follow_redirects=False)


def page_token(client, path):
    r = client.get(path)
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', r.get_data(as_text=True))
    assert m, f'csrf token not found at {path}'
    return m.group(1)


app = create_app()

# ---------------------------------------------------------------- H7
print('\n== H7: .gitignore ==')
gi = os.path.join(PROJECT, '.gitignore')
check('H7: .gitignore exists', os.path.exists(gi))
gi_text = open(gi).read() if os.path.exists(gi) else ''
for pattern in ('.env', '*.db', 'instance/*', 'uploaded_scripts/*', '__pycache__/'):
    check(f'H7: ignores {pattern}', pattern in gi_text)
check('H7: keeps instance placeholder txt', '!instance/*.txt' in gi_text)
check('H7: keeps upload placeholder txt', '!uploaded_scripts/*.txt' in gi_text)

# ---------------------------------------------------------------- H1
print('\n== H1: user create requires password ==')
with app.test_client() as client:
    login(client)
    token = page_token(client, '/admin/users/new')

    r = client.post('/admin/users/new', data={
        'username': 'carol', 'email': 'carol@example.com', 'password': '',
        'csrf_token': token,
    }, follow_redirects=False)
    with app.app_context():
        exists = User.query.filter_by(username='carol').first() is not None
    check('H1: blank password -> no 500, user NOT created',
          r.status_code == 200 and not exists,
          f'status={r.status_code} created={exists}')
    check('H1: error message shown',
          'Password is required' in r.get_data(as_text=True))

    token = page_token(client, '/admin/users/new')
    r = client.post('/admin/users/new', data={
        'username': 'carol', 'email': 'carol@example.com',
        'password': 's3cretpw', 'csrf_token': token,
    }, follow_redirects=False)
    with app.app_context():
        carol = User.query.filter_by(username='carol').first()
        ok = carol is not None and carol.check_password('s3cretpw')
    check('H1: with password -> created (302) and password works',
          r.status_code == 302 and ok, f'status={r.status_code}')

# ---------------------------------------------------------------- H2/H3/H4
print('\n== H2/H3/H4: script upload validation ==')
with app.test_client() as client:
    login(client)

    def create_schedule(name, file_data=None, filename=None):
        token = page_token(client, '/admin/schedules/new')
        data = {
            'job_name': name, 'schedule_type': 'daily',
            'schedule_time': '09:00', 'is_active': 'y', 'csrf_token': token,
        }
        if filename:
            data['script_file'] = (file_data, filename)
        r = client.post('/admin/schedules/new', data=data,
                        content_type='multipart/form-data',
                        follow_redirects=False)
        return r

    # H2: no file at all
    r = create_schedule('No File Job')
    with app.app_context():
        n = Schedule.query.filter_by(job_name='No File Job').count()
    check('H2: no upload -> rejected (200), nothing created',
          r.status_code == 200 and n == 0, f'status={r.status_code} n={n}')

    # H4: wrong extension
    r = create_schedule('Shell Job', io.BytesIO(b'echo hi\n'), 'payload.sh')
    with app.app_context():
        n = Schedule.query.filter_by(job_name='Shell Job').count()
    check('H4: non-.py upload -> rejected (200), nothing created',
          r.status_code == 200 and n == 0, f'status={r.status_code} n={n}')

    # H4: too large (>5MB)
    big = io.BytesIO(b'# pad\n' * (6 * 1024 * 1024 // 6 + 1))
    r = create_schedule('Big Job', big, 'big.py')
    with app.app_context():
        n = Schedule.query.filter_by(job_name='Big Job').count()
    check('H4: >5MB upload -> rejected (200), nothing created',
          r.status_code == 200 and n == 0, f'status={r.status_code} n={n}')

    # H2/H3: valid upload -> created with unique prefixed name
    r = create_schedule('First Main', io.BytesIO(b'print("one")\n'), 'main.py')
    with app.app_context():
        s1 = Schedule.query.filter_by(job_name='First Main').first()
    check('H2: valid upload -> created (302)',
          r.status_code == 302 and s1 is not None, f'status={r.status_code}')
    check('H3: filename prefixed with job number',
          s1 is not None and s1.script_filename.startswith(f'{s1.job_number}_'),
          f'filename={s1.script_filename if s1 else None}')
    p1 = os.path.join(SCRIPTS_DIR, s1.script_filename)
    CREATED_FILES.append(p1)
    check('H3: file exists on disk', os.path.exists(p1))

    # H3: second schedule, SAME original name -> must not collide
    r = create_schedule('Second Main', io.BytesIO(b'print("two")\n'), 'main.py')
    with app.app_context():
        s2 = Schedule.query.filter_by(job_name='Second Main').first()
    p2 = os.path.join(SCRIPTS_DIR, s2.script_filename) if s2 else ''
    CREATED_FILES.append(p2)
    check('H3: two uploads of main.py get distinct filenames',
          s2 is not None and s1.script_filename != s2.script_filename,
          f'{s1.script_filename if s1 else None} vs {s2.script_filename if s2 else None}')
    check('H3: both files exist and differ in content',
          os.path.exists(p1) and os.path.exists(p2)
          and open(p1).read() != open(p2).read())

    # H3/H4: edit upload also validated + prefixed
    token = page_token(client, f'/admin/schedules/{s1.id}/edit')
    r = client.post(f'/admin/schedules/{s1.id}/edit', data={
        'job_name': 'First Main', 'schedule_type': 'daily',
        'schedule_time': '09:00', 'is_active': 'y', 'csrf_token': token,
        'script_file': (io.BytesIO(b'echo nope\n'), 'evil.sh'),
    }, content_type='multipart/form-data', follow_redirects=False)
    with app.app_context():
        s1b = db.session.get(Schedule, s1.id)
    check('H4: edit with non-.py -> rejected, filename unchanged',
          r.status_code == 200 and s1b.script_filename == s1.script_filename,
          f'status={r.status_code} name={s1b.script_filename}')

    token = page_token(client, f'/admin/schedules/{s1.id}/edit')
    r = client.post(f'/admin/schedules/{s1.id}/edit', data={
        'job_name': 'First Main', 'schedule_type': 'daily',
        'schedule_time': '09:00', 'is_active': 'y', 'csrf_token': token,
        'script_file': (io.BytesIO(b'print("updated")\n'), 'main.py'),
    }, content_type='multipart/form-data', follow_redirects=False)
    with app.app_context():
        s1c = db.session.get(Schedule, s1.id)
    new_path = os.path.join(SCRIPTS_DIR, s1c.script_filename)
    CREATED_FILES.append(new_path)
    check('H3: edit upload gets schedule-scoped filename',
          r.status_code == 302 and s1c.script_filename.startswith(f'{s1.job_number}_'),
          f'status={r.status_code} name={s1c.script_filename}')
    check('H3: edited file content updated on disk',
          os.path.exists(new_path) and open(new_path).read() == 'print("updated")\n')

# ---------------------------------------------------------------- H6
print('\n== H6: cannot delete users who own schedules ==')
with app.app_context():
    admin = User.query.filter_by(username='admin').first()
    dave = User(username='dave', email='dave@example.com', is_admin=False,
                is_active=True)
    dave.set_password('pw123')
    db.session.add(dave)
    db.session.commit()
    sched = Schedule(job_number='TEST-DAVE', job_name='Daves Job',
                     script_filename='TEST-DAVE_x.py', schedule_type='daily',
                     schedule_time='08:00', is_active=True, created_by=dave.id)
    db.session.add(sched)
    db.session.commit()
    dave_id, sched_id = dave.id, sched.id

with app.test_client() as client:
    login(client)
    token = page_token(client, '/admin/users')
    r = client.post(f'/admin/users/{dave_id}/delete',
                    data={'csrf_token': token}, follow_redirects=True)
    body = r.get_data(as_text=True)
    with app.app_context():
        still_there = db.session.get(User, dave_id) is not None
    check('H6: owner deletion blocked (user survives)',
          still_there and 'Cannot delete' in body,
          f'alive={still_there}')

    # remove the schedule, then deletion works
    with app.app_context():
        db.session.delete(db.session.get(Schedule, sched_id))
        db.session.commit()
    token = page_token(client, '/admin/users')
    r = client.post(f'/admin/users/{dave_id}/delete',
                    data={'csrf_token': token}, follow_redirects=False)
    with app.app_context():
        gone = db.session.get(User, dave_id) is None
    check('H6: deletion allowed once schedules are gone', gone)

# ---------------------------------------------------------------- H5
print('\n== H5: one-time schedules ==')
from scheduler_service import _deactivate_if_once  # noqa: E402
import scheduler_service as ss  # noqa: E402

with app.app_context():
    # H5.1: month-end no longer crashes (old code: replace(day=day+1))
    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            base = cls(2026, 1, 31, 23, 0, 0)  # Jan 31, 23:00
            return base.replace(tzinfo=tz) if tz else base

    class OnceSched:
        schedule_type = 'once'
        schedule_time = '22:00'  # already past -> tomorrow = Feb 1
        schedule_day = None
        schedule_month = None
        interval_hours = None

    orig_dt = ss.datetime
    ss.datetime = FakeDT
    try:
        t = build_cron_expression(OnceSched())
        from apscheduler.triggers.date import DateTrigger
        ok_trigger = isinstance(t, DateTrigger)
        target = t.run_date
        check('H5: month-end once-schedule computes tomorrow safely (Jan 31 -> Feb 1)',
              ok_trigger and target.year == 2026 and target.month == 2
              and target.day == 1 and target.hour == 22,
              f'target={target}')
    finally:
        ss.datetime = orig_dt

    # H5.2: future time today, past time tomorrow (wrap-safe: compare
    # against the datetime the clock string would produce, which is
    # "future_dt" as-is and "past_dt + 1 day" once the time has passed)
    tz_utc = __import__('pytz').UTC
    now = datetime.now(tz_utc)
    future_dt = now + timedelta(hours=2)
    past_dt = now - timedelta(hours=2)

    OnceSched.schedule_time = future_dt.strftime('%H:%M')
    t = build_cron_expression(OnceSched())
    check('H5: future time -> later today (or tomorrow if it wrapped past midnight)',
          t.run_date.date() == future_dt.date(),
          f'{t.run_date} vs expected date {future_dt.date()}')
    OnceSched.schedule_time = past_dt.strftime('%H:%M')
    t = build_cron_expression(OnceSched())
    check('H5: past time -> tomorrow',
          t.run_date.date() == (past_dt + timedelta(days=1)).date(),
          f'{t.run_date} vs expected date {(past_dt + timedelta(days=1)).date()}')

    # H5.3: run_schedule deactivates a once-job after it runs
    quick = os.path.join(SCRIPTS_DIR, 'once_quick_tmp.py')
    with open(quick, 'w') as f:
        f.write('print("once ran")\n')
    CREATED_FILES.append(quick)
    admin = User.query.filter_by(username='admin').first()
    once = Schedule(job_number='TEST-ONCE', job_name='Once Job',
                    script_filename='once_quick_tmp.py', schedule_type='once',
                    schedule_time='23:59', is_active=True, created_by=admin.id)
    db.session.add(once)
    db.session.commit()
    once_id = once.id

    run_schedule(once_id, app=app)
    with app.app_context():
        o = db.session.get(Schedule, once_id)
        run = JobRun.query.filter_by(schedule_id=once_id).first()
    check('H5: once-job deactivated after run', o.is_active is False,
          f'is_active={o.is_active}')
    check('H5: once-job run recorded as completed',
          run is not None and run.status == 'completed',
          f'status={run.status if run else None}')
    check('H5: once-job output captured', run is not None
          and 'once ran' in (run.output_text or ''))

# ---------------------------------------------------------------- cleanup
for f in CREATED_FILES:
    try:
        if os.path.exists(f):
            os.remove(f)
    except OSError:
        pass

print(f'\n===== {len(PASS)} passed, {len(FAIL)} failed =====')
if FAIL:
    for f in FAIL:
        print(f'  FAILED: {f}')
    sys.exit(1)

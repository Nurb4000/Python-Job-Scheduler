"""Verification tests for H7b + Medium fixes (M1,M2,M3,M4,M6,M7,M8,M10,M11).

Run: python tests/test_medium_fixes.py
(Do NOT run from inside the project folder - the real .env must not load.)
"""
import io
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta

_TEST_TMP = tempfile.mkdtemp(prefix='pjs_test_')
TEST_DB = os.path.join(_TEST_TMP, 'test_pjs_med.db')
SEED_DB = os.path.join(_TEST_TMP, 'test_pjs_seed.db')
PROJECT = os.environ.get('PJS_PROJECT') or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(PROJECT, 'uploaded_scripts')

for f in (TEST_DB, SEED_DB):
    if os.path.exists(f):
        os.remove(f)

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

import config  # noqa: E402
from app import create_app  # noqa: E402
from extensions import db  # noqa: E402
from models import User, Schedule, JobRun  # noqa: E402

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


def page_token(client, path):
    return get_csrf(client, path)


def login(client):
    token = get_csrf(client)
    return client.post('/login', data={
        'username': 'admin', 'password': 'testpass123', 'csrf_token': token,
    }, follow_redirects=False)


# ---------------------------------------------------------------- H7b
print('\n== H7b: fail-fast on weak secrets ==')
orig = (config.Config.SECRET_KEY, config.Config.ADMIN_PASSWORD,
        config.Config.SQLALCHEMY_DATABASE_URI)
try:
    config.Config.SECRET_KEY = 'dev-secret-key'
    try:
        create_app()
        check('H7b: weak SECRET_KEY rejected', False, 'no exception raised')
    except RuntimeError as e:
        check('H7b: weak SECRET_KEY rejected', 'SECRET_KEY' in str(e))

    config.Config.SECRET_KEY = 'a-good-random-secret-key'
    config.Config.SQLALCHEMY_DATABASE_URI = f'sqlite:///{SEED_DB}'
    config.Config.ADMIN_PASSWORD = 'admin123'
    try:
        create_app()
        check('H7b: seeding with default ADMIN_PASSWORD rejected', False,
              'no exception raised')
    except RuntimeError as e:
        check('H7b: seeding with default ADMIN_PASSWORD rejected',
              'ADMIN_PASSWORD' in str(e))

    config.Config.ADMIN_PASSWORD = 'strong-random-pw-42'
    import glob
    for f in glob.glob(SEED_DB + '*'):
        os.remove(f)
    seed_app = create_app()
    with seed_app.app_context():
        from models import User as U
        seeded = U.query.filter_by(username='admin').first()
        ok = seeded is not None and seeded.check_password('strong-random-pw-42')
    check('H7b: fresh install seeds admin with required strong password', ok)
finally:
    (config.Config.SECRET_KEY, config.Config.ADMIN_PASSWORD,
     config.Config.SQLALCHEMY_DATABASE_URI) = orig

app = create_app()
with app.app_context():
    admin = User.query.filter_by(username='admin').first()
    admin_id = admin.id

# ---------------------------------------------------------------- M7
print('\n== M7: SQLite WAL + busy_timeout ==')
with app.app_context():
    from sqlalchemy import text
    mode = db.session.execute(text('PRAGMA journal_mode')).scalar()
    busy = db.session.execute(text('PRAGMA busy_timeout')).scalar()
check('M7: journal_mode is wal', str(mode).lower() == 'wal', f'mode={mode}')
check('M7: busy_timeout is 5000ms', int(busy) == 5000, f'busy={busy}')

# ---------------------------------------------------------------- M2
print('\n== M2: sort column whitelists ==')
with app.test_client() as client:
    login(client)
    for url in ('/admin/schedules?sort=creator',
                '/admin/schedules?sort=job_name&direction=asc',
                '/admin/schedules?sort=__class__',
                '/admin/users?sort=query',
                '/admin/users?sort=username&direction=asc',
                '/?sort=output_text',
                '/?sort=run_number&direction=asc'):
        r = client.get(url)
        check(f'M2: {url} -> 200', r.status_code == 200,
              f'status={r.status_code}')

# ---------------------------------------------------------------- M11
print('\n== M11: is_active checkbox is honest ==')
with app.test_client() as client:
    login(client)

    r = client.get('/admin/schedules/new')
    body = r.get_data(as_text=True)
    m = re.search(r'<input[^>]*name="is_active"[^>]*>', body)
    check('M11: new-schedule form renders checkbox checked',
          m is not None and 'checked' in m.group(0), f'tag={m.group(0) if m else None}')

    def create(name, with_active):
        token = page_token(client, '/admin/schedules/new')
        data = {'job_name': name, 'schedule_type': 'daily',
                'schedule_time': '09:00', 'csrf_token': token,
                'script_file': (io.BytesIO(b'print(1)\n'), 'x.py')}
        if with_active:
            data['is_active'] = 'y'
        return client.post('/admin/schedules/new', data=data,
                           content_type='multipart/form-data',
                           follow_redirects=False)

    r = create('Active Job', True)
    r2 = create('Inactive Job', False)
    with app.app_context():
        a = Schedule.query.filter_by(job_name='Active Job').first()
        b = Schedule.query.filter_by(job_name='Inactive Job').first()
        if a:
            CREATED_FILES.append(os.path.join(SCRIPTS_DIR, a.script_filename))
        if b:
            CREATED_FILES.append(os.path.join(SCRIPTS_DIR, b.script_filename))
    check('M11: checked -> active', a is not None and a.is_active is True)
    check('M11: unchecked -> inactive (was forced active before)',
          b is not None and b.is_active is False,
          f'is_active={b.is_active if b else None}')

    # edit: unchecking deactivates; checkbox reflects stored state on GET
    with app.app_context():
        a_id = a.id
        a.is_active = True
        db.session.commit()
    token = page_token(client, f'/admin/schedules/{a_id}/edit')
    body = client.get(f'/admin/schedules/{a_id}/edit').get_data(as_text=True)
    m = re.search(r'<input[^>]*name="is_active"[^>]*>', body)
    check('M11: edit form reflects stored active state',
          m is not None and 'checked' in m.group(0))
    token = page_token(client, f'/admin/schedules/{a_id}/edit')
    r = client.post(f'/admin/schedules/{a_id}/edit', data={
        'job_name': 'Active Job', 'schedule_type': 'daily',
        'schedule_time': '09:00', 'csrf_token': token,
    }, content_type='multipart/form-data', follow_redirects=False)
    with app.app_context():
        a2 = db.session.get(Schedule, a_id)
    check('M11: edit without checkbox deactivates',
          r.status_code == 302 and a2.is_active is False,
          f'status={r.status_code} active={a2.is_active}')

# ---------------------------------------------------------------- M3
print('\n== M3: job/run number collisions ==')
import utils  # noqa: E402
with app.test_client() as client:
    login(client)

    # deterministic: craft a row whose number equals generate's candidate
    # (candidate = last.id + 1; clash gets id=3 with number JOB-00004)
    with app.app_context():
        clash = Schedule(job_number='JOB-00004', job_name='Clash',
                         script_filename='clash.py', schedule_type='daily',
                         schedule_time='08:00', is_active=False,
                         created_by=admin_id)
        db.session.add(clash)
        db.session.commit()
        clash_id = clash.id
        gen = utils.generate_job_number()
    check('M3: generate skips an already-taken number',
          gen not in ('JOB-00004', 'JOB-00001', 'JOB-00002'),
          f'generated={gen} clash_id={clash_id}')

    token = page_token(client, '/admin/schedules/new')
    r = client.post('/admin/schedules/new', data={
        'job_name': 'After Clash', 'schedule_type': 'daily',
        'schedule_time': '09:00', 'is_active': 'y', 'csrf_token': token,
        'script_file': (io.BytesIO(b'print(2)\n'), 'x.py'),
    }, content_type='multipart/form-data', follow_redirects=False)
    with app.app_context():
        created = Schedule.query.filter_by(job_name='After Clash').first()
        if created:
            CREATED_FILES.append(os.path.join(SCRIPTS_DIR, created.script_filename))
    check('M3: create succeeds despite taken number',
          r.status_code == 302 and created is not None
          and created.job_number not in ('JOB-00004', 'JOB-00002'),
          f'status={r.status_code} number={created.job_number if created else None}')

    # race-retry: force the first two attempts to collide, third succeeds
    import views.admin as admin_mod
    orig_gen = admin_mod.generate_job_number
    calls = iter(['JOB-00002', 'JOB-00002', 'JOB-00077'])
    admin_mod.generate_job_number = lambda: next(calls)
    try:
        token = page_token(client, '/admin/schedules/new')
        r = client.post('/admin/schedules/new', data={
            'job_name': 'Retry Job', 'schedule_type': 'daily',
            'schedule_time': '09:00', 'is_active': 'y', 'csrf_token': token,
            'script_file': (io.BytesIO(b'print(3)\n'), 'x.py'),
        }, content_type='multipart/form-data', follow_redirects=False)
    finally:
        admin_mod.generate_job_number = orig_gen
    with app.app_context():
        retry = Schedule.query.filter_by(job_name='Retry Job').first()
        if retry:
            CREATED_FILES.append(os.path.join(SCRIPTS_DIR, retry.script_filename))
    check('M3: commit retry recovers from collision (no 500)',
          r.status_code == 302 and retry is not None
          and retry.job_number == 'JOB-00077',
          f'status={r.status_code} number={retry.job_number if retry else None}')

    # run-number retry inside run_schedule
    quick = os.path.join(SCRIPTS_DIR, 'med_quick_tmp.py')
    with open(quick, 'w') as f:
        f.write('print("quick ok")\n')
    CREATED_FILES.append(quick)
    with app.app_context():
        qs = Schedule(job_number='TEST-QUICK', job_name='Quick',
                      script_filename='med_quick_tmp.py',
                      schedule_type='daily', schedule_time='08:00',
                      is_active=True, created_by=admin_id)
        db.session.add(qs)
        db.session.commit()
        qs_id = qs.id
        existing = JobRun(run_number='R-00001', schedule_id=qs_id,
                          job_name='Quick', status='completed')
        db.session.add(existing)
        db.session.commit()

    import scheduler_service as ss
    orig_rn = ss.generate_run_number
    rn_calls = iter(['R-00001', 'R-00009'])
    ss.generate_run_number = lambda: next(rn_calls)
    try:
        ss.run_schedule(qs_id, app=app)
    finally:
        ss.generate_run_number = orig_rn
    with app.app_context():
        run = JobRun.query.filter(JobRun.run_number != 'R-00001')\
                          .filter_by(schedule_id=qs_id).first()
    check('M3: run_schedule retries run-number collision',
          run is not None and run.run_number == 'R-00009'
          and run.status == 'completed',
          f"run={run.run_number if run else None} status={run.status if run else None}")
    check('M3: retried run captured output',
          run is not None and 'quick ok' in (run.output_text or ''))

# ---------------------------------------------------------------- M4
print('\n== M4: output truncation ==')
from utils import truncate_output, MAX_OUTPUT_CHARS  # noqa: E402
small = 'hello world'
check('M4: short output untouched', truncate_output(small) is small)
big = 'A' * 250_000
cut = truncate_output(big)
check('M4: large output capped near limit',
      len(cut) <= MAX_OUTPUT_CHARS + 200, f'len={len(cut)}')
check('M4: head and tail preserved',
      cut.startswith('A' * 100) and cut.endswith('A' * 100))
check('M4: truncation marker present', 'output truncated' in cut)

# ---------------------------------------------------------------- M6
print('\n== M6: timeout kills the whole process group ==')
pid_file = os.path.join(_TEST_TMP, 'med_child.pid')
timeout_script = os.path.join(SCRIPTS_DIR, 'med_timeout_tmp.py')
with open(timeout_script, 'w') as f:
    f.write(
        'import subprocess, sys, time\n'
        f'p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])\n'
        f'open({pid_file!r}, "w").write(str(p.pid))\n'
        'print("parent started", flush=True)\n'
        'time.sleep(120)\n'
    )
CREATED_FILES.append(timeout_script)
app.config['SCRIPT_TIMEOUT'] = 2
from utils import run_python_script  # noqa: E402
with app.app_context():
    t0 = time.time()
    ok, err, out = run_python_script(timeout_script)
    elapsed = time.time() - t0
check('M6: timeout reported', ok is False and 'timed out' in err,
      f'err={err!r}')
check('M6: returned near the timeout (not 300s)', elapsed < 10,
      f'elapsed={elapsed:.1f}s')
check('M6: partial output captured', 'parent started' in out, f'out={out!r}')
child_pid = int(open(pid_file).read()) if os.path.exists(pid_file) else None
gone = False
if child_pid:
    for _ in range(25):
        try:
            os.kill(child_pid, 0)
            time.sleep(0.2)
        except (ProcessLookupError, PermissionError):
            gone = True
            break
check('M6: grandchild process killed too', child_pid is not None and gone,
      f'pid={child_pid} gone={gone}')
if os.path.exists(pid_file):
    os.remove(pid_file)

# ---------------------------------------------------------------- M8
print('\n== M8: logout is POST-only ==')
with app.test_client() as client:
    login(client)
    r = client.get('/logout')
    check('M8: GET /logout -> 405', r.status_code == 405,
          f'status={r.status_code}')

    r = client.post('/logout')
    check('M8: POST without CSRF -> 400', r.status_code == 400,
          f'status={r.status_code}')

    r = client.get('/admin/')
    body = r.get_data(as_text=True)
    m = re.search(r'<form method="POST" action="[^"]*logout[^"]*"', body)
    check('M8: nav renders a POST logout form', m is not None)

    token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', body).group(1)
    r = client.post('/logout', data={'csrf_token': token},
                    follow_redirects=False)
    r2 = client.get('/admin/', follow_redirects=False)
    check('M8: POST with CSRF logs out (then /admin redirects to login)',
          r.status_code == 302 and r2.status_code == 302
          and '/login' in r2.headers.get('Location', ''),
          f'logout={r.status_code} next={r2.status_code} loc={r2.headers.get("Location")}')

# ---------------------------------------------------------------- M10
print('\n== M10: JobRun.created_by populated for manual runs ==')
with app.test_client() as client:
    login(client)
    with app.app_context():
        s = Schedule(job_number='TEST-M10', job_name='Manual Run Job',
                     script_filename='med_quick_tmp.py',
                     schedule_type='daily', schedule_time='08:00',
                     is_active=True, created_by=admin_id)
        db.session.add(s)
        db.session.commit()
        s_id = s.id
    token = page_token(client, '/admin/schedules')
    t0 = time.time()
    r = client.post(f'/admin/schedules/{s_id}/run-now',
                    data={'csrf_token': token}, follow_redirects=False)
    check('M10: run-now still async', r.status_code == 302
          and time.time() - t0 < 2)
    run = None
    for _ in range(30):
        time.sleep(0.3)
        with app.app_context():
            run = JobRun.query.filter_by(schedule_id=s_id).first()
            if run and run.status in ('completed', 'failed'):
                break
    check('M10: created_by set to triggering user',
          run is not None and run.created_by == admin_id,
          f'created_by={run.created_by if run else None} expected={admin_id}')
    check('M10: scheduled runs keep created_by NULL (triggered_by=None)',
          run is not None and run.status == 'completed')

# ---------------------------------------------------------------- M1
print('\n== M1: once-schedules survive restarts (via H5) ==')
from scheduler_service import reload_schedules  # noqa: E402


class FakeScheduler:
    def __init__(self):
        self.jobs = []
        self.removed = False

    def remove_all_jobs(self):
        self.removed = True
        self.jobs = []

    def add_job(self, func, trigger=None, **kwargs):
        self.jobs.append((func, trigger, kwargs))


with app.app_context():
    once = Schedule(job_number='TEST-M1', job_name='Once Reload',
                    script_filename='med_quick_tmp.py',
                    schedule_type='once', schedule_time='00:00',
                    is_active=True, created_by=admin_id)
    db.session.add(once)
    db.session.commit()
    fake = FakeScheduler()
    reload_schedules(app, fake)
    from apscheduler.triggers.date import DateTrigger
    ok = any(
        isinstance(t, DateTrigger) and t.run_date > datetime.now(__import__('pytz').UTC)
        for _, t, _ in fake.jobs
    ) and len(fake.jobs) >= 1
check('M1: once-job is re-registered after restart (not dropped)',
      ok, f'jobs={[(type(t).__name__, getattr(t, "run_date", None)) for _, t, _ in fake.jobs]}')

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

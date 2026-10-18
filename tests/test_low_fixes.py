"""Verification tests for the Low/hygiene fixes (L1-L10).

Run: python tests/test_low_fixes.py
(Do NOT run from inside the project folder - the real .env must not load.)
"""
import glob
import io
import os
import re
import select
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta

PROJECT = os.environ.get('PJS_PROJECT') or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TEST_TMP = tempfile.mkdtemp(prefix='pjs_test_')
TEST_DB = os.path.join(_TEST_TMP, 'test_pjs_low.db')
SEED_DB = os.path.join(_TEST_TMP, 'test_pjs_low_seed.db')
L2_DB = os.path.join(_TEST_TMP, 'test_pjs_low_l2.db')
SCRIPTS_DIR = os.path.join(PROJECT, 'uploaded_scripts')

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
from models import User, Schedule, JobRun, AuditLog  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=''):
    if cond:
        PASS.append(name)
        print(f'  PASS  {name}')
    else:
        FAIL.append(name)
        print(f'  FAIL  {name}  {detail}')


def get_csrf(client, path):
    r = client.get(path)
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', r.get_data(as_text=True))
    assert m, f'csrf token not found at {path} (status {r.status_code})'
    return m.group(1)


def login(client, username, password):
    token = get_csrf(client, '/login')
    return client.post('/login', data={
        'username': username, 'password': password, 'csrf_token': token,
    })


app = create_app()
with app.app_context():
    admin_id = User.query.filter_by(username='admin').first().id
    for uname, mail in (('bob', 'bob@test.local'), ('carol', 'carol@test.local')):
        if not User.query.filter_by(username=uname).first():
            u = User(username=uname, email=mail, is_admin=False, is_active=True)
            u.set_password(f'{uname}pass123')
            db.session.add(u)
    db.session.commit()
    bob_id = User.query.filter_by(username='bob').first().id
    carol_id = User.query.filter_by(username='carol').first().id

# ---------------------------------------------------------------- L2
print('\n== L2: scheduler-only mode + deployment files ==')
l2_env = os.environ.copy()
l2_env['DATABASE_URL'] = f'sqlite:///{L2_DB}'
proc = subprocess.Popen(
    [sys.executable, 'app.py', 'scheduler', 'no-web'],
    cwd=PROJECT, env=l2_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True,
)
lines = []
deadline = time.time() + 20
while time.time() < deadline:
    ready, _, _ = select.select([proc.stdout], [], [], 1)
    if ready:
        line = proc.stdout.readline()
        if not line:
            break
        lines.append(line)
        if 'Scheduler started' in line:
            break
joined = ''.join(lines)
check('L2: `scheduler no-web` starts the scheduler and stays running',
      'Scheduler started' in joined and 'web disabled' in joined
      and proc.poll() is None, f'out={joined!r} rc={proc.poll()}')
proc.terminate()
try:
    proc.wait(timeout=10)
except subprocess.TimeoutExpired:
    proc.kill()
    proc.wait()

bad = subprocess.run(
    [sys.executable, 'app.py', 'no-web'], cwd=PROJECT, env=l2_env,
    capture_output=True, text=True, timeout=60,
)
check('L2: `no-web` without `scheduler` exits with an error',
      bad.returncode != 0 and 'only makes sense' in bad.stderr,
      f'rc={bad.returncode} err={bad.stderr[-200:]!r}')
check('L2: requirements-prod.txt ships gunicorn',
      os.path.isfile(os.path.join(PROJECT, 'requirements-prod.txt'))
      and 'gunicorn' in open(os.path.join(PROJECT, 'requirements-prod.txt')).read())
readme = open(os.path.join(PROJECT, 'README.md')).read()
check('L2: README documents the service/production setup',
      'Running as a service' in readme and 'gunicorn' in readme)

# ---------------------------------------------------------------- L3
print('\n== L3: no deprecated datetime.utcnow() ==')
from utils import now_utc  # noqa: E402
n = now_utc()
check('L3: now_utc() returns naive UTC',
      n.tzinfo is None and abs((datetime.utcnow() - n).total_seconds()) < 5,
      f'now={n}')
src_files = ['models.py', 'utils.py', 'scheduler_service.py',
             'views/admin.py', 'views/auth.py', 'views/jobs.py',
             'rate_limit.py', 'app.py', 'config.py', 'extensions.py']
hits = []
for f in src_files:
    text = open(os.path.join(PROJECT, f)).read()
    if re.search(r'datetime\.utcnow\(', text):
        hits.append(f)
check('L3: no datetime.utcnow() calls remain in source', not hits, f'hits={hits}')
with app.app_context():
    u = User.query.filter_by(username='bob').first()
    check('L3: model defaults still stamp naive datetimes',
          u.created_at is not None and u.created_at.tzinfo is None,
          f'created_at={u.created_at}')

# ---------------------------------------------------------------- L6
print('\n== L6: schedule day/month/interval validation ==')
with app.test_client() as client:
    login(client, 'admin', 'testpass123')

    def create(name, **fields):
        token = get_csrf(client, '/admin/schedules/new')
        data = {
            'job_name': name,
            'schedule_type': 'daily',
            'schedule_time': '08:00',
            'is_active': 'y',
            'csrf_token': token,
            'script_file': (io.BytesIO(b'print("ok")\n'), 'main.py'),
        }
        data.update(fields)
        return client.post('/admin/schedules/new', data=data,
                           content_type='multipart/form-data')

    with app.app_context():
        before = Schedule.query.count()
    r = create('weekly-bad', schedule_type='weekly', schedule_day='9')
    body = r.get_data(as_text=True)
    with app.app_context():
        after = Schedule.query.count()
    check('L6: weekly day=9 rejected with clear message, nothing created',
          r.status_code == 200 and 'must be 0-6' in body and after == before,
          f'status={r.status_code} body={body[:150]!r}')

    r = create('weekly-ok', schedule_type='weekly', schedule_day='3')
    check('L6: weekly day=3 accepted', r.status_code == 302, f'status={r.status_code}')

    r = create('monthly-day0', schedule_type='monthly', schedule_day='0')
    check('L6: monthly day=0 rejected',
          r.status_code == 200 and '1-31' in r.get_data(as_text=True),
          f'status={r.status_code}')

    r = create('quarterly-month13', schedule_type='quarterly',
               schedule_day='1', schedule_month='13')
    check('L6: quarterly month=13 rejected',
          r.status_code == 200 and '1-12' in r.get_data(as_text=True),
          f'status={r.status_code}')

    r = create('hourly-zero', schedule_type='hourly', interval_hours='0')
    check('L6: interval_hours=0 rejected',
          r.status_code == 200 and 'at least 1' in r.get_data(as_text=True),
          f'status={r.status_code}')

    r = create('monthly-ok', schedule_type='monthly', schedule_day='15')
    check('L6: monthly day=15 accepted', r.status_code == 302,
          f'status={r.status_code}')

# ---------------------------------------------------------------- L4 / L8
print('\n== L4: uploaded-script cleanup; L8: audit log ==')
with app.test_client() as client:
    login(client, 'admin', 'testpass123')

    token = get_csrf(client, '/admin/schedules/new')
    r = client.post('/admin/schedules/new', data={
        'job_name': 'audit-job', 'schedule_type': 'daily',
        'schedule_time': '09:00', 'is_active': 'y', 'csrf_token': token,
        'script_file': (io.BytesIO(b'print("v1")\n'), 'main.py'),
    }, content_type='multipart/form-data')
    check('L4: schedule with upload created', r.status_code == 302,
          f'status={r.status_code}')
    with app.app_context():
        sched = Schedule.query.filter_by(job_name='audit-job').first()
        sched_id = sched.id
        job_number = sched.job_number
        v1_path = os.path.join(SCRIPTS_DIR, sched.script_filename)
    check('L4: uploaded file exists after create', os.path.isfile(v1_path),
          f'path={v1_path}')

    token = get_csrf(client, f'/admin/schedules/{sched_id}/edit')
    r = client.post(f'/admin/schedules/{sched_id}/edit', data={
        'job_name': 'audit-job', 'schedule_type': 'daily',
        'schedule_time': '09:00', 'is_active': 'y', 'csrf_token': token,
        'script_file': (io.BytesIO(b'print("v2")\n'), 'v2.py'),
    }, content_type='multipart/form-data')
    with app.app_context():
        sched = db.session.get(Schedule, sched_id)
        v2_path = os.path.join(SCRIPTS_DIR, sched.script_filename)
    check('L4: re-upload replaces file, old file removed',
          r.status_code == 302 and os.path.isfile(v2_path)
          and not os.path.isfile(v1_path),
          f'status={r.status_code} v1={os.path.isfile(v1_path)} v2={os.path.isfile(v2_path)}')

    token = get_csrf(client, f'/admin/schedules/{sched_id}/edit')
    r = client.post(f'/admin/schedules/{sched_id}/edit', data={
        'job_name': 'audit-job-renamed', 'schedule_type': 'daily',
        'schedule_time': '09:00', 'is_active': 'y', 'csrf_token': token,
    }, content_type='multipart/form-data')
    check('L6/L8: edit without upload keeps the file',
          r.status_code == 302 and os.path.isfile(v2_path),
          f'status={r.status_code}')

    token = get_csrf(client, '/admin/schedules')
    r = client.post(f'/admin/schedules/{sched_id}/delete', data={'csrf_token': token})
    check('L4: uploaded file removed when schedule deleted',
          r.status_code == 302 and not os.path.isfile(v2_path),
          f'status={r.status_code} exists={os.path.isfile(v2_path)}')

    with app.app_context():
        actions = [e.action for e in AuditLog.query.all()]
    check('L8: schedule create/update/delete all audited',
          'schedule.create' in actions and 'schedule.update' in actions
          and 'schedule.delete' in actions, f'actions={actions}')

    token = get_csrf(client, '/admin/users/new')
    r = client.post('/admin/users/new', data={
        'username': 'audituser', 'email': 'audituser@example.com',
        'password': 'auditpass123', 'csrf_token': token,
    })
    check('L8: user created', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        audit_user = User.query.filter_by(username='audituser').first()
        audit_user_id = audit_user.id
        eids = [e.action for e in AuditLog.query.all()]
    check('L8: user create audited', 'user.create' in eids, f'actions={eids}')

    token = get_csrf(client, f'/admin/users/{audit_user_id}/edit')
    r = client.post(f'/admin/users/{audit_user_id}/edit', data={
        'username': 'audituser2', 'email': 'audituser@example.com',
        'csrf_token': token,
    })
    token = get_csrf(client, '/admin/users')
    r = client.post(f'/admin/users/{audit_user_id}/delete', data={'csrf_token': token})
    with app.app_context():
        e = AuditLog.query.filter_by(action='user.update').first()
        e2 = AuditLog.query.filter_by(action='user.delete').first()
    check('L8: user update audited with rename detail',
          e is not None and 'renamed from audituser' in (e.detail or ''),
          f'detail={e.detail if e else None}')
    check('L8: user delete audited', e2 is not None and e2.target == 'audituser2')

    r = client.get('/admin/audit')
    body = r.get_data(as_text=True)
    check('L8: /admin/audit page renders with entries',
          r.status_code == 200 and 'schedule.create' in body
          and 'audituser' in body, f'status={r.status_code}')

# ---------------------------------------------------------------- L7
print('\n== L7: non-admin POSTs get 403, GETs still redirect ==')
with app.test_client() as client:
    login(client, 'bob', 'bobpass123')
    r = client.get('/admin/')
    check('L7: non-admin GET /admin/ still redirects (302)',
          r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        any_sched = Schedule.query.first()
        sid = any_sched.id if any_sched else 1
    token = get_csrf(client, '/')
    r = client.post(f'/admin/schedules/{sid}/run-now', data={'csrf_token': token})
    check('L7: non-admin POST run-now gets 403', r.status_code == 403,
          f'status={r.status_code}')
    r = client.post(f'/admin/schedules/{sid}/delete', data={'csrf_token': token})
    check('L7: non-admin POST delete gets 403', r.status_code == 403,
          f'status={r.status_code}')
    r = client.get('/admin/audit')
    check('L7: non-admin GET /admin/audit redirects (302)',
          r.status_code == 302, f'status={r.status_code}')

# ---------------------------------------------------------------- L5
print('\n== L5: failure notifications reach creator AND viewers ==')
import scheduler_service as ss  # noqa: E402
sent = []
orig_send = ss.send_email
ss.send_email = lambda to, subj, body: sent.append((to, subj, body))
try:
    with app.app_context():
        sched = Schedule(
            job_number='TEST-NOTIFY', job_name='Notify',
            script_filename='definitely_missing_script_xyz.py',
            schedule_type='daily', schedule_time='08:00', is_active=True,
            created_by=admin_id,
        )
        sched.viewers = [db.session.get(User, bob_id), db.session.get(User, carol_id)]
        db.session.add(sched)
        db.session.commit()
        sid = sched.id
    ss.run_schedule(sid, app)
finally:
    ss.send_email = orig_send
recipients = sorted(t for t, _, _ in sent)
check('L5: creator and all viewers are notified of the failure',
      recipients == ['admin@test.local', 'bob@test.local', 'carol@test.local'],
      f'recipients={recipients}')

# ---------------------------------------------------------------- L9
print('\n== L9: env-sample has no usable admin password; placeholders refused ==')
env_sample = open(os.path.join(PROJECT, 'env-sample')).read()
check('L9: env-sample ADMIN_PASSWORD is blank',
      re.search(r'^ADMIN_PASSWORD=\s*$', env_sample, re.M) is not None
      and 'admin123' not in env_sample,
      env_sample[:200])
orig = (config.Config.SECRET_KEY, config.Config.ADMIN_PASSWORD,
        config.Config.SQLALCHEMY_DATABASE_URI)
try:
    config.Config.SECRET_KEY = 'a-good-random-secret-key'
    config.Config.SQLALCHEMY_DATABASE_URI = f'sqlite:///{SEED_DB}'
    for weak in ('changeme', 'password', 'letmein'):
        config.Config.ADMIN_PASSWORD = weak
        try:
            create_app()
            check(f'L9: seeding with placeholder ADMIN_PASSWORD {weak!r} rejected',
                  False, 'no exception raised')
        except RuntimeError:
            check(f'L9: seeding with placeholder ADMIN_PASSWORD {weak!r} rejected',
                  True)
finally:
    (config.Config.SECRET_KEY, config.Config.ADMIN_PASSWORD,
     config.Config.SQLALCHEMY_DATABASE_URI) = orig

# ---------------------------------------------------------------- L10
print('\n== L10: gitignore keeps secrets/DB/uploads out ==')
probe = subprocess.run(['git', '-C', PROJECT, 'rev-parse', '--is-inside-work-tree'],
                       capture_output=True, text=True)
if probe.returncode != 0:
    check('L10: gitignore verification (skipped - not a git work tree)', True)
else:
    ignored = ['.env', 'instance/foo.db', 'uploaded_scripts/evil.py',
               '.pytest_cache/x', '__pycache__/x.pyc']
    for path in ignored:
        r = subprocess.run(['git', '-C', PROJECT, 'check-ignore', '-q', path])
        check(f'L10: {path} is ignored', r.returncode == 0)
    tracked_ok = subprocess.run(
        ['git', '-C', PROJECT, 'check-ignore', '-q', 'uploaded_scripts/uploads wil go here.txt'])
    check('L10: placeholder .txt in uploaded_scripts stays tracked',
          tracked_ok.returncode == 1, f'rc={tracked_ok.returncode}')

# ---------------------------------------------------------------- L1
print('\n== L1: test suite lives in the repo with CI ==')
check('L1: tests/ contains all four earlier suites plus this one',
      all(os.path.isfile(os.path.join(PROJECT, 'tests', f)) for f in (
          'test_critical_fixes.py', 'test_high_fixes.py',
          'test_medium_fixes.py', 'test_deferred_mediums.py',
          'test_low_fixes.py', 'test_suites.py')),
      os.listdir(os.path.join(PROJECT, 'tests')))
check('L1: pytest wrapper + dev requirements exist',
      os.path.isfile(os.path.join(PROJECT, 'requirements-dev.txt')))
check('L1: GitHub Actions workflow exists',
      os.path.isfile(os.path.join(PROJECT, '.github', 'workflows', 'tests.yml')))

# ---------------------------------------------------------------- cleanup + summary
with app.app_context():
    for pattern in ('med_quick_tmp.py', 'med_timeout_tmp.py', 'high_*.py',
                    'crit_*.py', 'sleeper_test_tmp.py'):
        for f in glob.glob(os.path.join(SCRIPTS_DIR, pattern)):
            try:
                os.remove(f)
            except OSError:
                pass

print(f'\n===== {len(PASS)} passed, {len(FAIL)} failed =====')
if FAIL:
    for f in FAIL:
        print(f'  FAILED: {f}')
    sys.exit(1)

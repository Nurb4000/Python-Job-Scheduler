"""Verification tests for the deferred Medium items: M5 (JobRun retention)
and M9 (login rate limiting).

Run: python tests/test_deferred_mediums.py
(Do NOT run from inside the project folder - the real .env must not load.)
"""
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta

_TEST_TMP = tempfile.mkdtemp(prefix='pjs_test_')
TEST_DB = os.path.join(_TEST_TMP, 'test_pjs_def.db')
PROJECT = os.environ.get('PJS_PROJECT') or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
    'LOGIN_MAX_ATTEMPTS': '5',
    'LOGIN_WINDOW_SECONDS': '900',
    'JOB_RUN_RETENTION_DAYS': '0',
})

sys.path.insert(0, PROJECT)

from app import create_app  # noqa: E402
from extensions import db  # noqa: E402
from models import User, Schedule, JobRun  # noqa: E402
from rate_limit import RateLimiter, login_limiter  # noqa: E402
from scheduler_service import prune_job_runs, reload_schedules  # noqa: E402

PASS, FAIL = [], []


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


def attempt_login(client, username, password):
    token = get_csrf(client)
    return client.post('/login', data={
        'username': username, 'password': password, 'csrf_token': token,
    })


app = create_app()

# ---------------------------------------------------------------- M9
print('\n== M9: login rate limiting ==')
with app.app_context():
    bob = User(username='bob', email='bob@test.local', is_admin=False,
               is_active=True)
    bob.set_password('bobpass123')
    db.session.add(bob)
    db.session.commit()

with app.test_client() as client:
    r = client.get('/login')
    check('M9: login page renders', r.status_code == 200)

    for i in range(5):
        r = attempt_login(client, 'admin', f'wrong{i}')
    check('M9: 5 failed attempts still get normal 400-avoiding response',
          r.status_code == 200 and 'Invalid username or password.'
          in r.get_data(as_text=True),
          f'status={r.status_code}')

    r = attempt_login(client, 'admin', 'wrong-again')
    body = r.get_data(as_text=True)
    check('M9: 6th attempt is locked out with 429',
          r.status_code == 429 and 'Too many failed attempts' in body,
          f'status={r.status_code}')

    r = attempt_login(client, 'admin', 'testpass123')
    check('M9: correct password still rejected while locked out',
          r.status_code == 429, f'status={r.status_code}')

    r = attempt_login(client, 'someotheruser', 'whatever')
    check('M9: lockout is per-username, other keys unaffected',
          r.status_code == 200, f'status={r.status_code}')

    # --- reset-on-success: bob fails, logs in, logs out, fails again ---
    r = attempt_login(client, 'bob', 'bad1')
    r = attempt_login(client, 'bob', 'bad2')
    r = attempt_login(client, 'bob', 'bad3')
    check('M9: bob initial failures are normal 200s',
          r.status_code == 200, f'status={r.status_code}')
    r = attempt_login(client, 'bob', 'bobpass123')
    check('M9: bob can still log in', r.status_code == 302,
          f'status={r.status_code}')
    token = get_csrf(client, '/')
    r = client.post('/logout', data={'csrf_token': token})
    check('M9: bob logged out', r.status_code == 302, f'status={r.status_code}')
    for i in range(3):
        r = attempt_login(client, 'bob', f'bad-again-{i}')
    check('M9: failures reset after a successful login '
          '(3rd of second batch not locked out)',
          r.status_code == 200 and 'Invalid username or password.'
          in r.get_data(as_text=True),
          f'status={r.status_code} body={r.get_data(as_text=True)[:120]!r}')

    # --- window expiry (direct unit test, tiny window) ---
    rl = RateLimiter()
    key = 'ip|windowtest'
    rl.record_failure(key, 1.0)
    rl.record_failure(key, 1.0)
    allowed, retry_after = rl.check(key, 2, 1.0)
    check('M9: limiter blocks at the limit with retry_after > 0',
          not allowed and retry_after > 0, f'allowed={allowed} retry={retry_after}')
    time.sleep(1.1)
    allowed, _ = rl.check(key, 2, 1.0)
    check('M9: lockout expires after the window', allowed)

    # --- disabled via config ---
    app.config['LOGIN_MAX_ATTEMPTS'] = 0
    statuses = [attempt_login(client, 'admin', f'nope{i}').status_code
                for i in range(8)]
    check('M9: LOGIN_MAX_ATTEMPTS=0 disables limiting (all 200)',
          statuses == [200] * 8, f'statuses={statuses}')
    app.config['LOGIN_MAX_ATTEMPTS'] = 5
    login_limiter.reset('127.0.0.1|admin')

    r = attempt_login(client, 'admin', 'testpass123')
    check('M9: admin can still log in after tests', r.status_code == 302,
          f'status={r.status_code}')

# ---------------------------------------------------------------- M5
print('\n== M5: JobRun retention ==')
with app.app_context():
    admin_id = User.query.filter_by(username='admin').first().id
    sched = Schedule(job_number='TEST-KEEP', job_name='Keep',
                     script_filename='none.py', schedule_type='daily',
                     schedule_time='08:00', is_active=False,
                     created_by=admin_id)
    db.session.add(sched)
    db.session.commit()
    sched_id = sched.id

    old = datetime.utcnow() - timedelta(days=40)
    recent = datetime.utcnow() - timedelta(days=1)
    runs = [
        # completed, older than a 30-day window -> pruned
        JobRun(run_number='R-OLD-DONE', schedule_id=sched_id,
               job_name='Keep', started_at=old, completed_at=old,
               status='completed'),
        # completed, recent -> kept
        JobRun(run_number='R-RECENT-DONE', schedule_id=sched_id,
               job_name='Keep', started_at=recent, completed_at=recent,
               status='completed'),
        # stuck 'running' since 40 days ago (completed_at NULL) -> pruned
        JobRun(run_number='R-OLD-STUCK', schedule_id=sched_id,
               job_name='Keep', started_at=old, completed_at=None,
               status='running'),
        # running recently -> kept
        JobRun(run_number='R-RECENT-STUCK', schedule_id=sched_id,
               job_name='Keep', started_at=recent, completed_at=None,
               status='running'),
    ]
    db.session.add_all(runs)
    db.session.commit()

    # default retention = 0 -> keep everything
    deleted = prune_job_runs(app)
    remaining = JobRun.query.count()
    check('M5: retention 0 (default) prunes nothing',
          deleted == 0 and remaining == 4,
          f'deleted={deleted} remaining={remaining}')

    app.config['JOB_RUN_RETENTION_DAYS'] = 30
    deleted = prune_job_runs(app)
    numbers = {r.run_number for r in JobRun.query.all()}
    check('M5: retention 30 prunes exactly the old rows',
          deleted == 2 and numbers == {'R-RECENT-DONE', 'R-RECENT-STUCK'},
          f'deleted={deleted} numbers={numbers}')

    # startup-style call works while a context is already active (nested)
    with app.app_context():
        deleted = prune_job_runs(app)
    check('M5: prune is a no-op when nothing is old enough', deleted == 0,
          f'deleted={deleted}')

    app.config['JOB_RUN_RETENTION_DAYS'] = 0

    # the daily maintenance job is registered by reload_schedules and
    # survives alongside the per-schedule jobs
    from apscheduler.schedulers.background import BackgroundScheduler
    sched_backend = BackgroundScheduler()
    reload_schedules(app, sched_backend)
    jobs = {j.id for j in sched_backend.get_jobs()}
    check('M5: reload_schedules registers the retention job',
          'job_run_retention' in jobs, f'jobs={jobs}')
    sched.is_active = True
    db.session.commit()
    reload_schedules(app, sched_backend)
    jobs = {j.id for j in sched_backend.get_jobs()}
    check('M5: retention job survives a reload with active schedules',
          'job_run_retention' in jobs and f'schedule_{sched_id}' in jobs,
          f'jobs={jobs}')
    sched.is_active = False
    db.session.commit()

# ---------------------------------------------------------------- summary
print(f'\n===== {len(PASS)} passed, {len(FAIL)} failed =====')
if FAIL:
    for f in FAIL:
        print(f'  FAILED: {f}')
    sys.exit(1)

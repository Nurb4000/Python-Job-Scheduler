"""Verification tests for the critical fixes (C1-C5) in Python-Job-Scheduler.

Run: python tests/test_critical_fixes.py
"""
import os
import re
import sys
import tempfile
import time
import shutil

# --- configure environment BEFORE importing the app ---
PROJECT = os.environ.get('PJS_PROJECT') or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_DB = os.path.join(tempfile.mkdtemp(prefix='pjs_test_'), 'test_pjs.db')
SCRIPTS_DIR = os.path.join(PROJECT, 'uploaded_scripts')
SLEEPER = os.path.join(SCRIPTS_DIR, 'sleeper_test_tmp.py')

for f in (TEST_DB,):
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

from app import create_app  # noqa: E402
from extensions import db  # noqa: E402
from models import User, Schedule, JobRun  # noqa: E402
from scheduler_service import build_cron_expression  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, detail=''):
    if cond:
        PASS.append(name)
        print(f'  PASS  {name}')
    else:
        FAIL.append(name)
        print(f'  FAIL  {name}  {detail}')


def get_csrf(client):
    r = client.get('/login')
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', r.get_data(as_text=True))
    assert m, 'csrf token not found on login page'
    return m.group(1)


def login(client, username, password, token):
    return client.post('/login', data={
        'username': username, 'password': password, 'csrf_token': token,
    }, follow_redirects=False)


def do_logout(client):
    # M8: logout is POST-only; the token comes from the base-nav logout form
    r = client.get('/admin/')
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', r.get_data(as_text=True))
    assert m, 'csrf token not found for logout'
    return client.post('/logout', data={'csrf_token': m.group(1)})


app = create_app()

# ---------------------------------------------------------------- C1 / C2
print('\n== C1/C2: debug off, no double scheduler ==')
src = open(os.path.join(PROJECT, 'app.py')).read()
check('C1: no hardcoded debug=True in app.py', 'debug=True' not in src)
check('C1: FLASK_DEBUG env-driven with false default',
      "os.getenv('FLASK_DEBUG', 'false')" in src)
check('C2: reloader double-start guarded via WERKZEUG_RUN_MAIN',
      'WERKZEUG_RUN_MAIN' in src)
check('C1: app.debug is False after create_app', app.debug is False)

# ---------------------------------------------------------------- C5
print('\n== C5: open redirect after login ==')
with app.test_client() as client:
    token = get_csrf(client)
    r = login(client, 'admin', 'testpass123', token)
    check('C5: login succeeds', r.status_code == 302)
    check('C5: no-next goes to admin dashboard',
          r.headers['Location'].endswith('/admin/'))

    do_logout(client)

    token = get_csrf(client)
    r = client.post('/login?next=//evil.example/phish', data={
        'username': 'admin', 'password': 'testpass123', 'csrf_token': token,
    }, follow_redirects=False)
    loc = r.headers['Location']
    check('C5: protocol-relative next rejected',
          'evil.example' not in loc, f'Location={loc}')

    do_logout(client)
    token = get_csrf(client)
    r = client.post('/login?next=https://evil.example/x', data={
        'username': 'admin', 'password': 'testpass123', 'csrf_token': token,
    }, follow_redirects=False)
    loc = r.headers['Location']
    check('C5: absolute https next rejected', 'evil.example' not in loc,
          f'Location={loc}')

    do_logout(client)
    token = get_csrf(client)
    r = client.post('/login?next=/admin/schedules', data={
        'username': 'admin', 'password': 'testpass123', 'csrf_token': token,
    }, follow_redirects=False)
    check('C5: same-host relative next still allowed',
          r.headers['Location'].endswith('/admin/schedules'),
          f"Location={r.headers['Location']}")

# ---------------------------------------------------------------- C4
print('\n== C4: CSRF enforced on raw POST routes ==')
with app.app_context():
    admin = User.query.filter_by(username='admin').first()
    bob = User(username='bob', email='bob@test.local', is_admin=False,
               is_active=True)
    bob.set_password('bobpass123')
    sched = Schedule(job_number='TEST-A', job_name='Dummy',
                     script_filename='dummy.py', schedule_type='daily',
                     schedule_time='08:00', is_active=True, created_by=admin.id)
    db.session.add_all([bob, sched])
    db.session.commit()
    sched_id, bob_id, admin_id = sched.id, bob.id, admin.id

with app.test_client() as client:
    r = client.post(f'/admin/schedules/{sched_id}/delete')
    check('C4: schedule delete without token -> 400', r.status_code == 400)
    r = client.post(f'/admin/users/{bob_id}/delete')
    check('C4: user delete without token -> 400', r.status_code == 400)
    r = client.post(f'/admin/schedules/{sched_id}/run-now')
    check('C4: run-now without token -> 400', r.status_code == 400)

    # authenticated + token works
    token = get_csrf(client)
    r = login(client, 'admin', 'testpass123', token)
    r = client.get('/admin/schedules')
    token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"',
                      r.get_data(as_text=True)).group(1)
    r = client.post(f'/admin/schedules/{sched_id}/delete',
                    data={'csrf_token': token}, follow_redirects=False)
    check('C4: schedule delete with token succeeds (302)',
          r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        gone = db.session.get(Schedule, sched_id) is None
    check('C4: schedule actually deleted', gone)

# ---------------------------------------------------------------- C3
print('\n== C3: Run Now is asynchronous ==')
with open(SLEEPER, 'w') as f:
    f.write('import time\ntime.sleep(2.5)\nprint("sleeper done")\n')

with app.app_context():
    admin = User.query.filter_by(username='admin').first()
    s2 = Schedule(job_number='TEST-B', job_name='Sleeper',
                  script_filename='sleeper_test_tmp.py', schedule_type='daily',
                  schedule_time='08:00', is_active=True, created_by=admin.id)
    db.session.add(s2)
    db.session.commit()
    s2_id = s2.id

try:
    with app.test_client() as client:
        token = get_csrf(client)
        login(client, 'admin', 'testpass123', token)
        r = client.get('/admin/schedules')
        token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"',
                          r.get_data(as_text=True)).group(1)
        t0 = time.time()
        r = client.post(f'/admin/schedules/{s2_id}/run-now',
                        data={'csrf_token': token}, follow_redirects=False)
        elapsed = time.time() - t0
        check('C3: run-now returns immediately (<1.5s while script sleeps 2.5s)',
              r.status_code == 302 and elapsed < 1.5,
              f'status={r.status_code} elapsed={elapsed:.2f}s')

        # poll for the background run to finish
        status = None
        for _ in range(40):
            time.sleep(0.4)
            with app.app_context():
                run = JobRun.query.filter_by(schedule_id=s2_id).first()
                if run and run.status in ('completed', 'failed'):
                    status = run
                    break
        check('C3: background run completed', status is not None and
              status.status == 'completed',
              f'status={status.status if status else None}')
        if status:
            check('C3: script output captured',
                  'sleeper done' in (status.output_text or ''),
                  f'output={status.output_text!r}')
finally:
    if os.path.exists(SLEEPER):
        os.remove(SLEEPER)

# ---------------------------------------------------------------- regressions
print('\n== regressions ==')
def month_exprs(trigger):
    return [str(e) for e in
            [f for f in trigger.fields if f.name == 'month'][0].expressions]


with app.app_context():
    class FakeSched:
        schedule_type = 'quarterly'
        schedule_time = '08:00'
        schedule_day = None
        schedule_month = 10
        interval_hours = None
    t = build_cron_expression(FakeSched())
    check('quarterly month wrap still correct (10 -> 10,1,4,7)',
          month_exprs(t) == ['10', '1', '4', '7'],
          f"{month_exprs(t)}")
    FakeSched.schedule_month = None
    t = build_cron_expression(FakeSched())
    check('quarterly default still 1,4,7,10',
          month_exprs(t) == ['1', '4', '7', '10'],
          f"{month_exprs(t)}")

# CSRF still fine on regular forms (login + multipart schedule create)
with app.test_client() as client:
    token = get_csrf(client)
    login(client, 'admin', 'testpass123', token)
    r = client.get('/admin/schedules/new')
    token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"',
                      r.get_data(as_text=True)).group(1)
    import io
    data = {
        'job_name': 'Uploaded Job',
        'schedule_type': 'daily',
        'schedule_time': '09:30',
        'is_active': 'y',
        'csrf_token': token,
        'script_file': (io.BytesIO(b'print("hi")\n'), 'upload_test.py'),
    }
    r = client.post('/admin/schedules/new', data=data,
                    content_type='multipart/form-data', follow_redirects=False)
    check('schedule create with upload + CSRF still works',
          r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        created = Schedule.query.filter_by(job_name='Uploaded Job').first()
        check('uploaded schedule persisted', created is not None)
        if created:
            p = os.path.join(SCRIPTS_DIR, created.script_filename)
            if os.path.exists(p):
                os.remove(p)

# ---------------------------------------------------------------- summary
print(f'\n===== {len(PASS)} passed, {len(FAIL)} failed =====')
if FAIL:
    for f in FAIL:
        print(f'  FAILED: {f}')
    sys.exit(1)

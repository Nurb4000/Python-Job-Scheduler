Simple job scheduling system for python scripts. Nothing real fancy, just enough to get the job done and not have to mess with cron jobs or something and have a GUI for status and such.

- Lets you upload a script to run, and specify when to run it, and if it repeats or not.  
- Allows user to 'own' the script and they can login to see current status and previous runs and view console outputs. Only admins can schedule / run for security reasons.
- On errors, it will email the owners. 
- Be sure to edit the .env file for your smtp server settings and choose a better default admin password.
- Use SQLite for the back-end DB.

Running

- `python app.py` — web UI only.
- `python app.py scheduler` — web UI **plus** the background scheduler. Run this on exactly one host, or every job will fire twice.
- Copy `env-sample` to `.env` and set your values. Leave `FLASK_DEBUG` unset or `false` outside local development.
- The server binds to `HOST`/`PORT` from `.env` (default `0.0.0.0:5000`). Put it behind a reverse proxy with HTTPS for anything beyond a trusted LAN.
- Login brute-force protection is on by default (`LOGIN_MAX_ATTEMPTS` failed logins per IP+username per `LOGIN_WINDOW_SECONDS`, then a temporary lockout). Set `LOGIN_MAX_ATTEMPTS=0` to disable.
- Job-run history is kept forever by default. Set `JOB_RUN_RETENTION_DAYS` (e.g. `90`) to have the scheduler prune older run rows daily.

Running as a service / in production

- The built-in server (`python app.py`) is Flask's development server — fine on a trusted LAN, not for the public internet. Put it behind a reverse proxy with HTTPS, and pick one of:
    - **All-in-one (simplest):** run `python app.py scheduler` under systemd — web UI and scheduler in one process; jobs fire on that host.
    - **Split:** serve the web UI with gunicorn (`pip install -r requirements-prod.txt`, then `gunicorn -w 1 -b 127.0.0.1:5000 'app:create_app()'`) and run the scheduler on **exactly one** host with `python app.py scheduler no-web`. Keep gunicorn workers at 1 — the SQLite database and the in-memory login rate limiter are per-process.
- systemd unit for the all-in-one host (`/etc/systemd/system/pjscheduler.service`):

      [Unit]
      Description=Python Job Scheduler
      After=network.target

      [Service]
      WorkingDirectory=/opt/python-job-scheduler
      ExecStart=/opt/python-job-scheduler/venv/bin/python app.py scheduler
      Restart=on-failure

      [Install]
      WantedBy=multi-user.target

  On a dedicated scheduler host use `ExecStart=... app.py scheduler no-web` instead.

- Schedule Options
    - One time (runs at the next occurrence of the chosen time, then deactivates itself)
    - Hourly
    - Daily
    - Weekly
    - Weekdays
    - Monthly
    - Every 3 months (Quarterly)
    - Yearly

A couple of screenshots of the interface


<img width="1371" height="510" alt="image" src="https://github.com/user-attachments/assets/e89429ee-a0f4-401f-97c2-784ca624cb06" />
<img width="1326" height="359" alt="image" src="https://github.com/user-attachments/assets/a5825b59-7686-4824-97bf-94bcb2be5604" />
<img width="1346" height="555" alt="image" src="https://github.com/user-attachments/assets/4f1ba420-41a5-42f9-915a-98ed20e6b62f" />
<img width="1349" height="397" alt="image" src="https://github.com/user-attachments/assets/fdc37af5-f08e-4487-8078-dacfa1eb4622" />
<img width="1371" height="446" alt="image" src="https://github.com/user-attachments/assets/34649156-6525-419b-bef1-00e2eaa625bb" />





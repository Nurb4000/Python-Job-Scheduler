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

- Schedule Options
    - One time
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





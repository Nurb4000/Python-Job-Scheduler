import os
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Schedule, JobRun, User
from utils import (
    generate_run_number, run_python_script, send_email,
    format_datetime, now_utc, get_tz, truncate_output
)


def run_schedule(schedule_id, app=None, triggered_by=None):
    if app is None:
        from app import create_app
        app = create_app()

    with app.app_context():
        schedule = Schedule.query.get(schedule_id)
        if not schedule or not schedule.is_active:
            return

        run_number = generate_run_number()
        job_name = schedule.job_name

        # Allocate a run number; retry if a concurrent run wins the race.
        job_run = None
        for _ in range(5):
            job_run = JobRun(
                run_number=run_number,
                schedule_id=schedule.id,
                job_name=job_name,
                started_at=now_utc(),
                status='running',
                created_by=triggered_by
            )
            db.session.add(job_run)
            try:
                db.session.commit()
                break
            except IntegrityError:
                db.session.rollback()
                job_run = None
                run_number = generate_run_number()
        if job_run is None:
            app.logger.error(f'Failed to allocate run number for schedule {schedule_id}')
            return

        try:
            script_path = os.path.join(app.root_path, 'uploaded_scripts', schedule.script_filename)
            if not os.path.exists(script_path):
                job_run.status = 'failed'
                job_run.error_message = f'Script file not found: {script_path}'
                job_run.completed_at = now_utc()
                db.session.commit()
                _notify_owner(app, schedule, job_run)
                _deactivate_if_once(app, schedule)
                return

            success, error_msg, output = run_python_script(script_path)
            job_run.output_text = truncate_output(output)
            job_run.completed_at = now_utc()

            if success:
                job_run.status = 'completed'
            else:
                job_run.status = 'failed'
                job_run.error_message = error_msg

            db.session.commit()

            if not success:
                _notify_owner(app, schedule, job_run)

            schedule.last_run_at = now_utc()
            db.session.commit()
            _deactivate_if_once(app, schedule)
        except Exception as e:
            app.logger.error(f'Error running schedule {schedule_id} ({run_number}): {e}')
            try:
                job_run.status = 'failed'
                job_run.error_message = str(e)
                job_run.completed_at = now_utc()
                db.session.commit()
            except Exception:
                db.session.rollback()
            _deactivate_if_once(app, schedule)


def _deactivate_if_once(app, schedule):
    """One-time schedules run exactly once, then switch themselves off."""
    if schedule.schedule_type != 'once' or not schedule.is_active:
        return
    try:
        schedule.is_active = False
        db.session.commit()
        refresh_scheduler(app)
    except Exception as e:
        db.session.rollback()
        app.logger.error(f'Failed to deactivate once-schedule {schedule.id}: {e}')


def _notify_owner(app, schedule, job_run):
    owner = schedule.creator
    if owner and owner.email:
        send_email(
            owner.email,
            f'Job Failed: {schedule.job_name} ({job_run.run_number})',
            f'Schedule: {schedule.job_number}\n'
            f'Job: {schedule.job_name}\n'
            f'Run: {job_run.run_number}\n'
            f'Error: {job_run.error_message}\n'
            f'Time: {format_datetime(job_run.started_at)}'
        )


def build_cron_expression(schedule):
    schedule_type = schedule.schedule_type
    time_str = schedule.schedule_time
    try:
        hour, minute = time_str.split(':')
        hour = int(hour)
        minute = int(minute)
    except (ValueError, AttributeError):
        hour, minute = 8, 0

    day = schedule.schedule_day or None
    month = schedule.schedule_month or None

    if schedule_type == 'once':
        # "One Time" = next occurrence of schedule_time (today if still
        # ahead, otherwise tomorrow). The job deactivates itself after run.
        tz = get_tz()
        now = datetime.now(tz)
        run_date = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if run_date <= now:
            run_date += timedelta(days=1)
        return DateTrigger(run_date=run_date, timezone=tz)

    if schedule_type == 'hourly':
        tz = get_tz()
        now = datetime.now(tz)
        start_date = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if start_date <= now:
            start_date = start_date.replace(day=start_date.day + 1)
        interval = getattr(schedule, 'interval_hours', None) or 1
        return IntervalTrigger(hours=interval, start_date=start_date, timezone=tz)

    cron_kwargs = {'hour': hour, 'minute': minute, 'timezone': get_tz()}

    if schedule_type == 'daily':
        pass
    elif schedule_type == 'weekdays':
        cron_kwargs['day_of_week'] = 'mon-fri'
    elif schedule_type == 'weekly':
        cron_kwargs['day_of_week'] = day or 'mon'
    elif schedule_type == 'monthly':
        cron_kwargs['day'] = day or 1
    elif schedule_type == 'quarterly':
        cron_kwargs['day'] = day or 1
        if month and 1 <= month <= 12:
            cron_kwargs['month'] = ','.join(
                str(((month - 1 + i * 3) % 12) + 1) for i in range(4)
            )
        else:
            cron_kwargs['month'] = '1,4,7,10'
    elif schedule_type == 'yearly':
        cron_kwargs['month'] = month or 1
        cron_kwargs['day'] = day or 1

    return CronTrigger(**cron_kwargs)


def prune_job_runs(app):
    """Delete JobRun rows older than JOB_RUN_RETENTION_DAYS.

    Returns the number deleted; no-op when retention is 0 (keep forever).
    Age is measured from completed_at, falling back to started_at so rows
    stuck in 'running' are pruned too.
    """
    days = int(app.config.get('JOB_RUN_RETENTION_DAYS', 0) or 0)
    if days <= 0:
        return 0
    with app.app_context():
        cutoff = now_utc() - timedelta(days=days)
        age = func.coalesce(JobRun.completed_at, JobRun.started_at)
        deleted = (JobRun.query.filter(age < cutoff)
                   .delete(synchronize_session=False))
        db.session.commit()
        if deleted:
            app.logger.info(f'Pruned {deleted} job run(s) older than {days} days')
        return deleted


def reload_schedules(app, scheduler):
    with app.app_context():
        scheduler.remove_all_jobs()
        # Maintenance job — remove_all_jobs() wipes it too, so re-add here
        # so it survives every refresh (create/edit/delete of schedules).
        scheduler.add_job(
            prune_job_runs,
            'interval',
            days=1,
            args=[app],
            id='job_run_retention',
            replace_existing=True,
            misfire_grace_time=86400,
        )
        schedules = Schedule.query.filter_by(is_active=True).all()
        for sched in schedules:
            # 'once' is handled by build_cron_expression too: it schedules the
            # next occurrence of schedule_time, keeping save-time and
            # restart-time behavior identical (the old special case here
            # scheduled a different day and silently dropped passed jobs).
            try:
                trigger = build_cron_expression(sched)
                scheduler.add_job(
                    run_schedule,
                    trigger=trigger,
                    args=[sched.id, app],
                    id=f'schedule_{sched.id}',
                    replace_existing=True,
                    misfire_grace_time=300
                )
            except Exception as e:
                app.logger.error(f'Failed to schedule {sched.job_number}: {e}')


def start_scheduler(app):
    scheduler = BackgroundScheduler()
    scheduler.start()
    reload_schedules(app, scheduler)
    app.scheduler = scheduler
    # Prune once at startup too, so long-idle installs clean up immediately
    # instead of waiting up to a day.
    try:
        prune_job_runs(app)
    except Exception as e:
        app.logger.error(f'Job-run retention prune failed at startup: {e}')
    return scheduler


def refresh_scheduler(app):
    scheduler = getattr(app, 'scheduler', None)
    if scheduler:
        reload_schedules(app, scheduler)

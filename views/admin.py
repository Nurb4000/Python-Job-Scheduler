import os
import threading
from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, send_file
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from models import User, Schedule, JobRun
from forms import ScheduleForm, ScheduleFilterForm, UserForm
from extensions import db
from utils import generate_job_number, format_datetime, now_utc
from scheduler_service import refresh_scheduler

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            flash('Admin access required.', 'danger')
            return redirect(url_for('jobs.history'))
        return f(*args, **kwargs)
    return decorated


@admin_bp.route('/')
@login_required
@admin_required
def dashboard():
    total_schedules = Schedule.query.count()
    active_schedules = Schedule.query.filter_by(is_active=True).count()
    total_runs = JobRun.query.count()
    failed_runs = JobRun.query.filter_by(status='failed').count()
    recent_runs = JobRun.query.order_by(JobRun.started_at.desc()).limit(10).all()
    return render_template('admin/dashboard.html',
        total_schedules=total_schedules, active_schedules=active_schedules,
        total_runs=total_runs, failed_runs=failed_runs, recent_runs=recent_runs,
        format_datetime=format_datetime)


@admin_bp.route('/schedules')
@login_required
@admin_required
def schedules():
    form = ScheduleFilterForm(request.args)
    query = Schedule.query

    search = request.args.get('search', '').strip()
    if search:
        like = f'%{search}%'
        query = query.filter(
            db.or_(
                Schedule.job_number.ilike(like),
                Schedule.job_name.ilike(like),
                Schedule.script_filename.ilike(like)
            )
        )

    if request.args.get('schedule_type'):
        query = query.filter(Schedule.schedule_type == request.args['schedule_type'])
    if request.args.get('is_active') == 'active':
        query = query.filter(Schedule.is_active == True)
    elif request.args.get('is_active') == 'inactive':
        query = query.filter(Schedule.is_active == False)

    sort = request.args.get('sort', 'id')
    direction = request.args.get('direction', 'desc')
    sort_col = getattr(Schedule, sort, Schedule.id)
    if direction == 'desc':
        sort_col = sort_col.desc()
    query = query.order_by(sort_col)

    page = request.args.get('page', 1, type=int)
    pagination = query.paginate(page=page, per_page=25, error_out=False)
    return render_template('admin/schedules.html',
        form=form, pagination=pagination, schedules=pagination.items,
        format_datetime=format_datetime, sort=sort, direction=direction,
        search=search)


@admin_bp.route('/schedules/new', methods=['GET', 'POST'])
@login_required
@admin_required
def schedule_new():
    form = ScheduleForm()
    form.viewers.choices = [(u.id, f'{u.username} ({u.email})') for u in User.query.filter_by(is_active=True).order_by(User.username).all()]

    if form.validate_on_submit():
        script_file = request.files.get('script_file')
        script_filename = form.job_name.data.replace(' ', '_')
        if script_file and script_file.filename:
            filename = secure_filename(script_file.filename)
            scripts_dir = os.path.join(current_app.root_path, 'uploaded_scripts')
            os.makedirs(scripts_dir, exist_ok=True)
            filepath = os.path.join(scripts_dir, filename)
            script_file.save(filepath)
            script_filename = filename

        schedule = Schedule(
            job_number=generate_job_number(),
            job_name=form.job_name.data,
            script_filename=script_filename,
            schedule_type=form.schedule_type.data,
            schedule_time=form.schedule_time.data,
            is_active=form.is_active.data if 'is_active' in request.form else True,
            created_by=current_user.id
        )

        try:
            schedule.schedule_day = int(form.schedule_day.data) if form.schedule_day.data else None
        except (ValueError, TypeError):
            schedule.schedule_day = None
        try:
            schedule.schedule_month = int(form.schedule_month.data) if form.schedule_month.data else None
        except (ValueError, TypeError):
            schedule.schedule_month = None
        try:
            schedule.interval_hours = int(form.interval_hours.data) if form.interval_hours.data else None
        except (ValueError, TypeError):
            schedule.interval_hours = None

        schedule.viewers = User.query.filter(User.id.in_(form.viewers.data or [])).all()
        db.session.add(schedule)
        db.session.commit()
        refresh_scheduler(current_app._get_current_object())
        flash(f'Schedule {schedule.job_number} created.', 'success')
        return redirect(url_for('admin.schedules'))

    return render_template('admin/schedule_form.html', form=form, title='New Schedule')


@admin_bp.route('/schedules/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def schedule_edit(id):
    schedule = Schedule.query.get_or_404(id)
    form = ScheduleForm(obj=schedule)
    form.viewers.choices = [(u.id, f'{u.username} ({u.email})') for u in User.query.filter_by(is_active=True).order_by(User.username).all()]

    if request.method == 'GET':
        form.viewers.data = [u.id for u in schedule.viewers]
        form.schedule_day.data = str(schedule.schedule_day) if schedule.schedule_day else ''
        form.schedule_month.data = str(schedule.schedule_month) if schedule.schedule_month else ''
        form.interval_hours.data = schedule.interval_hours
        form.script_filename.data = schedule.script_filename

    if form.validate_on_submit():
        script_file = request.files.get('script_file')
        if script_file and script_file.filename:
            filename = secure_filename(script_file.filename)
            scripts_dir = os.path.join(current_app.root_path, 'uploaded_scripts')
            os.makedirs(scripts_dir, exist_ok=True)
            filepath = os.path.join(scripts_dir, filename)
            script_file.save(filepath)
            schedule.script_filename = filename

        schedule.job_name = form.job_name.data
        schedule.schedule_type = form.schedule_type.data
        schedule.schedule_time = form.schedule_time.data
        schedule.is_active = form.is_active.data if 'is_active' in request.form else False

        try:
            schedule.schedule_day = int(form.schedule_day.data) if form.schedule_day.data else None
        except (ValueError, TypeError):
            schedule.schedule_day = None
        try:
            schedule.schedule_month = int(form.schedule_month.data) if form.schedule_month.data else None
        except (ValueError, TypeError):
            schedule.schedule_month = None
        try:
            schedule.interval_hours = int(form.interval_hours.data) if form.interval_hours.data else None
        except (ValueError, TypeError):
            schedule.interval_hours = None

        schedule.viewers = User.query.filter(User.id.in_(form.viewers.data or [])).all()
        db.session.commit()
        refresh_scheduler(current_app._get_current_object())
        flash(f'Schedule {schedule.job_number} updated.', 'success')
        return redirect(url_for('admin.schedules'))

    return render_template('admin/schedule_form.html', form=form, title='Edit Schedule', schedule=schedule)


@admin_bp.route('/schedules/<int:id>/delete', methods=['POST'])
@login_required
@admin_required
def schedule_delete(id):
    schedule = Schedule.query.get_or_404(id)
    job_number = schedule.job_number
    db.session.delete(schedule)
    db.session.commit()
    refresh_scheduler(current_app._get_current_object())
    flash(f'Schedule {job_number} deleted.', 'success')
    return redirect(url_for('admin.schedules'))


@admin_bp.route('/schedules/<int:id>/run-now', methods=['POST'])
@login_required
@admin_required
def schedule_run_now(id):
    schedule = Schedule.query.get_or_404(id)
    job_number = schedule.job_number
    from scheduler_service import run_schedule
    app = current_app._get_current_object()
    threading.Thread(
        target=run_schedule, args=(schedule.id, app), daemon=True
    ).start()
    flash(f'Schedule {job_number} triggered. Refresh to see results in history.', 'info')
    return redirect(url_for('admin.schedules'))


@admin_bp.route('/schedules/<int:id>/download-script')
@login_required
@admin_required
def schedule_download_script(id):
    schedule = Schedule.query.get_or_404(id)
    script_path = os.path.join(current_app.root_path, 'uploaded_scripts', schedule.script_filename)
    if not os.path.exists(script_path):
        flash('Script file not found on disk.', 'danger')
        return redirect(url_for('admin.schedules'))
    return send_file(script_path, as_attachment=True, download_name=schedule.script_filename)


@admin_bp.route('/users')
@login_required
@admin_required
def users():
    search = request.args.get('search', '').strip()
    query = User.query
    if search:
        like = f'%{search}%'
        query = query.filter(
            db.or_(
                User.username.ilike(like),
                User.email.ilike(like)
            )
        )
    sort = request.args.get('sort', 'id')
    direction = request.args.get('direction', 'desc')
    sort_col = getattr(User, sort, User.id)
    if direction == 'desc':
        sort_col = sort_col.desc()
    query = query.order_by(sort_col)

    page = request.args.get('page', 1, type=int)
    pagination = query.paginate(page=page, per_page=25, error_out=False)
    return render_template('admin/users.html',
        pagination=pagination, users=pagination.items,
        format_datetime=format_datetime, sort=sort, direction=direction,
        search=search)


@admin_bp.route('/users/new', methods=['GET', 'POST'])
@login_required
@admin_required
def user_new():
    form = UserForm()
    if form.validate_on_submit():
        if User.query.filter_by(username=form.username.data).first():
            flash('Username already exists.', 'danger')
            return render_template('admin/user_form.html', form=form, title='New User')
        if User.query.filter_by(email=form.email.data).first():
            flash('Email already exists.', 'danger')
            return render_template('admin/user_form.html', form=form, title='New User')
        user = User(
            username=form.username.data,
            email=form.email.data,
            is_admin=form.is_admin.data,
            is_active=form.is_active.data if 'is_active' in request.form else True
        )
        if form.password.data:
            user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        flash(f'User {user.username} created.', 'success')
        return redirect(url_for('admin.users'))
    return render_template('admin/user_form.html', form=form, title='New User')


@admin_bp.route('/users/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def user_edit(id):
    user = User.query.get_or_404(id)
    form = UserForm(obj=user)
    if form.validate_on_submit():
        if form.username.data != user.username and User.query.filter_by(username=form.username.data).first():
            flash('Username already exists.', 'danger')
            return render_template('admin/user_form.html', form=form, title='Edit User', user=user)
        if form.email.data != user.email and User.query.filter_by(email=form.email.data).first():
            flash('Email already exists.', 'danger')
            return render_template('admin/user_form.html', form=form, title='Edit User', user=user)
        user.username = form.username.data
        user.email = form.email.data
        user.is_admin = form.is_admin.data
        user.is_active = form.is_active.data if 'is_active' in request.form else False
        if form.password.data:
            user.set_password(form.password.data)
        db.session.commit()
        flash(f'User {user.username} updated.', 'success')
        return redirect(url_for('admin.users'))
    return render_template('admin/user_form.html', form=form, title='Edit User', user=user)


@admin_bp.route('/users/<int:id>/delete', methods=['POST'])
@login_required
@admin_required
def user_delete(id):
    user = User.query.get_or_404(id)
    if user.id == current_user.id:
        flash('Cannot delete yourself.', 'danger')
        return redirect(url_for('admin.users'))
    username = user.username
    db.session.delete(user)
    db.session.commit()
    flash(f'User {username} deleted.', 'success')
    return redirect(url_for('admin.users'))

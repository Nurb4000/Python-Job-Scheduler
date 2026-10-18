from urllib.parse import urljoin, urlparse

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_user, logout_user, login_required, current_user
from models import User
from forms import LoginForm
from extensions import db
from rate_limit import login_limiter

auth_bp = Blueprint('auth', __name__)


def is_safe_url(target):
    if not target:
        return False
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return (
        test_url.scheme in ('http', 'https')
        and ref_url.netloc == test_url.netloc
    )


def _rate_limit_key(username):
    ip = request.remote_addr or 'unknown'
    return f'{ip}|{(username or "").strip().lower()}'


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('jobs.history'))
    form = LoginForm()

    max_attempts = int(current_app.config.get('LOGIN_MAX_ATTEMPTS', 5) or 0)
    window = int(current_app.config.get('LOGIN_WINDOW_SECONDS', 900) or 0)
    rate_key = None
    if request.method == 'POST' and max_attempts > 0 and window > 0:
        rate_key = _rate_limit_key(form.username.data)
        allowed, retry_after = login_limiter.check(rate_key, max_attempts, window)
        if not allowed:
            return render_template('login.html', form=form,
                                   rate_limited=retry_after), 429

    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and user.check_password(form.password.data):
            if not user.is_active:
                flash('Account is disabled.', 'danger')
                return render_template('login.html', form=form)
            if rate_key:
                login_limiter.reset(rate_key)
            login_user(user)
            next_page = request.args.get('next')
            if not is_safe_url(next_page):
                next_page = None
            if current_user.is_admin:
                return redirect(next_page or url_for('admin.dashboard'))
            return redirect(next_page or url_for('jobs.history'))
        if rate_key:
            login_limiter.record_failure(rate_key, window)
        flash('Invalid username or password.', 'danger')
    return render_template('login.html', form=form)


@auth_bp.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))

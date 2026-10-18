from urllib.parse import urljoin, urlparse

from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from models import User
from forms import LoginForm
from extensions import db

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


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('jobs.history'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and user.check_password(form.password.data):
            if not user.is_active:
                flash('Account is disabled.', 'danger')
                return render_template('login.html', form=form)
            login_user(user)
            next_page = request.args.get('next')
            if not is_safe_url(next_page):
                next_page = None
            if current_user.is_admin:
                return redirect(next_page or url_for('admin.dashboard'))
            return redirect(next_page or url_for('jobs.history'))
        flash('Invalid username or password.', 'danger')
    return render_template('login.html', form=form)


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))

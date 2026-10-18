from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from extensions import db
from utils import now_utc


schedule_viewers = db.Table('schedule_viewers',
    db.Column('schedule_id', db.Integer, db.ForeignKey('schedule.id'), primary_key=True),
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True)
)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=now_utc)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Schedule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    job_number = db.Column(db.String(20), unique=True, nullable=False)
    job_name = db.Column(db.String(200), nullable=False, index=True)
    script_filename = db.Column(db.String(300), nullable=False)
    schedule_type = db.Column(db.String(20), nullable=False)
    schedule_day = db.Column(db.Integer, nullable=True)
    schedule_month = db.Column(db.Integer, nullable=True)
    schedule_time = db.Column(db.String(5), nullable=False)
    interval_hours = db.Column(db.Integer, nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=now_utc)
    updated_at = db.Column(db.DateTime, default=now_utc, onupdate=now_utc)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    last_run_at = db.Column(db.DateTime, nullable=True)

    creator = db.relationship('User', backref='created_schedules', foreign_keys=[created_by])
    viewers = db.relationship('User', secondary=schedule_viewers, lazy='subquery',
        backref=db.backref('viewable_schedules', lazy=True))
    runs = db.relationship('JobRun', backref='schedule', lazy='dynamic',
        cascade='all, delete-orphan')


class JobRun(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    run_number = db.Column(db.String(20), unique=True, nullable=False)
    schedule_id = db.Column(db.Integer, db.ForeignKey('schedule.id'), nullable=False)
    job_name = db.Column(db.String(200), nullable=False, index=True)
    started_at = db.Column(db.DateTime, nullable=False, default=now_utc)
    completed_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), nullable=False, default='pending')
    error_message = db.Column(db.Text, nullable=True)
    output_text = db.Column(db.Text, nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    creator = db.relationship('User', backref='job_runs', foreign_keys=[created_by])


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=now_utc, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    # Username captured at action time, so history survives user deletion.
    user_label = db.Column(db.String(80), nullable=False)
    action = db.Column(db.String(40), nullable=False, index=True)
    target = db.Column(db.String(200), nullable=False)
    detail = db.Column(db.Text, nullable=True)

    user = db.relationship('User', backref='audit_entries', foreign_keys=[user_id])

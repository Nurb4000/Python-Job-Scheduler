from flask_wtf import FlaskForm
from flask_wtf.file import FileField
from wtforms import (
    StringField, PasswordField, SelectField, SelectMultipleField,
    BooleanField, SubmitField, HiddenField, IntegerField
)
from wtforms.validators import DataRequired, Email, Length, Optional


class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Sign In')


class UserForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=80)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[Optional(), Length(min=4, max=256)])
    is_admin = BooleanField('Administrator')
    is_active = BooleanField('Active')
    submit = SubmitField('Save')


class ScheduleForm(FlaskForm):
    id = HiddenField('id')
    job_name = StringField('Job Name', validators=[DataRequired(), Length(max=200)])
    script_filename = HiddenField('script_filename')
    script_file = FileField('Python Script')
    schedule_type = SelectField('Schedule', choices=[
        ('once', 'One Time'),
        ('hourly', 'Hourly'),
        ('daily', 'Daily'),
        ('weekdays', 'Weekdays (Mon-Fri)'),
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
        ('quarterly', 'Every 3 Months (Quarterly)'),
        ('yearly', 'Yearly')
    ], validators=[DataRequired()])
    schedule_day = StringField('Day of Month / Day of Week')
    schedule_month = StringField('Month')
    interval_hours = IntegerField('Every X Hours', validators=[Optional()])
    schedule_time = StringField('Time (HH:MM)', validators=[DataRequired()])
    viewers = SelectMultipleField('Viewers', coerce=int)
    is_active = BooleanField('Active')
    submit = SubmitField('Save Schedule')


class ScheduleFilterForm(FlaskForm):
    search = StringField('Search')
    schedule_type = SelectField('Schedule', choices=[
        ('', 'All Schedules'),
        ('once', 'One Time'),
        ('hourly', 'Hourly'),
        ('daily', 'Daily'),
        ('weekdays', 'Weekdays (Mon-Fri)'),
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
        ('quarterly', 'Every 3 Months (Quarterly)'),
        ('yearly', 'Yearly')
    ])
    is_active = SelectField('Status', choices=[
        ('', 'All'),
        ('active', 'Active'),
        ('inactive', 'Inactive')
    ])

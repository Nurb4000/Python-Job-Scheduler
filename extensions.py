import sqlite3

from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import event
from sqlalchemy.engine import Engine

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
csrf = CSRFProtect()


def _configure_sqlite(dbapi_connection, _record):
    """WAL keeps the scheduler thread and web threads from blocking each
    other; busy_timeout stops immediate 'database is locked' errors."""
    if isinstance(dbapi_connection, sqlite3.Connection):
        cur = dbapi_connection.cursor()
        cur.execute('PRAGMA journal_mode=WAL')
        cur.execute('PRAGMA busy_timeout=5000')
        cur.close()


event.listen(Engine, 'connect', _configure_sqlite)

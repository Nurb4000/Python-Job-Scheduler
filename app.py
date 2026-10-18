import os
import sys
from flask import Flask
from dotenv import load_dotenv

load_dotenv()


def create_app():
    app = Flask(__name__)
    app.config.from_object('config.Config')

    from extensions import db, login_manager, csrf
    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    from models import User
    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    from views.auth import auth_bp
    from views.admin import admin_bp
    from views.jobs import jobs_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(jobs_bp)

    with app.app_context():
        db.create_all()
        _ensure_admin(app)

    return app


def _ensure_admin(app):
    from models import User
    from extensions import db
    admin = User.query.filter_by(is_admin=True).first()
    if not admin:
        admin = User(
            username='admin',
            email=app.config['ADMIN_EMAIL'],
            is_admin=True,
            is_active=True
        )
        admin.set_password(app.config['ADMIN_PASSWORD'])
        db.session.add(admin)
        db.session.commit()


if __name__ == '__main__':
    app = create_app()
    debug = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    # With the Werkzeug reloader, __main__ executes in both the parent and the
    # child process. Only start the scheduler in the child (or always, when the
    # reloader is off) so jobs don't get registered and fired twice.
    if 'scheduler' in sys.argv and (
        not debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
    ):
        from scheduler_service import start_scheduler
        scheduler = start_scheduler(app)
        print(f'Scheduler started with {len(scheduler.get_jobs())} jobs')
    app.run(
        host=os.getenv('HOST', '0.0.0.0'),
        port=int(os.getenv('PORT', '5000')),
        debug=debug,
    )

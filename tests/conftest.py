import os
import tempfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from werkzeug.security import generate_password_hash


@pytest.fixture
def app():
    """Isolated Flask app + unique SQLite file under /tmp.

    Forces a fresh SQLAlchemy engine on the process-wide ``db`` extension so
    tests cannot share tables across fixtures (avoids UNIQUE email collisions).
    """
    fd, db_path = tempfile.mkstemp(prefix="signix_test_", suffix=".db", dir="/tmp")
    os.close(fd)
    uri = f"sqlite:///{db_path}"
    os.environ["DATABASE_URL"] = uri
    os.environ["SECRET_KEY"] = "test-secret-key"

    from app import create_app
    from app.models import User, db

    application = create_app()
    application.config["TESTING"] = True
    application.config["WTF_CSRF_ENABLED"] = False
    application.config["SECRET_KEY"] = "test-secret-key"
    application.config["SQLALCHEMY_DATABASE_URI"] = uri

    with application.app_context():
        # Tear down any engine left from a previous test and bind a new one.
        try:
            db.session.remove()
        except Exception:
            pass
        try:
            db.engine.dispose()
        except Exception:
            pass
        if hasattr(db, "_engines"):
            db._engines.clear()
        if hasattr(db, "_app_engines"):
            # Flask-SQLAlchemy 3 stores engines here keyed by app
            try:
                db._app_engines.pop(application, None)
            except Exception:
                pass

        engine = create_engine(uri, poolclass=NullPool)
        # Flask-SQLAlchemy 3: register engine for this app
        if hasattr(db, "_app_engines"):
            from flask import Flask

            db._app_engines.setdefault(application, {})[None] = engine
        else:
            db.engine = engine  # type: ignore[attr-defined]

        db.session.configure(bind=engine)
        db.create_all()

        db.session.add(
            User(
                email="test@example.com",
                password_hash=generate_password_hash(
                    "unused", method="pbkdf2:sha256"
                ),
                display_name="Test User",
            )
        )
        db.session.commit()

    yield application

    with application.app_context():
        try:
            db.session.remove()
        except Exception:
            pass
        try:
            db.engine.dispose()
        except Exception:
            pass
        if hasattr(db, "_engines"):
            db._engines.clear()
        if hasattr(db, "_app_engines"):
            try:
                db._app_engines.pop(application, None)
            except Exception:
                pass
    os.environ.pop("DATABASE_URL", None)
    try:
        os.unlink(db_path)
    except OSError:
        pass


@pytest.fixture
def client(app):
    test_client = app.test_client()
    response = test_client.post(
        "/login",
        data={"email": "test@example.com", "password": "unused"},
        follow_redirects=False,
    )
    if response.status_code != 302:
        # Fallback: inject session user id (mirrors a successful login_user)
        with app.app_context():
            from app.models import User
            user = User.query.filter_by(email="test@example.com").first()
            uid = user.id if user else 1
        with test_client.session_transaction() as sess:
            sess["_user_id"] = str(uid)
            sess["_fresh"] = True
        # Verify
        probe = test_client.get("/dashboard", follow_redirects=False)
        if probe.status_code not in (200, 302):
            body = response.get_data(as_text=True)
            raise AssertionError(
                f"Login expected 302, got {response.status_code}. Body: {body[:400]}"
            )
    return test_client


@pytest.fixture
def app_context(app):
    with app.app_context() as context:
        yield context

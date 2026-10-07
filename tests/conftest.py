import os

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["ADMIN_PASSWORD"] = ""

import pytest

from app.db import Base, SessionLocal, engine


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

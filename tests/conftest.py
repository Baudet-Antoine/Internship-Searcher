import os

import pytest

from stage_radar import db


@pytest.fixture(scope="session")
def database_url(tmp_path_factory):
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        yield url
        return
    pgserver = pytest.importorskip("pgserver")
    server = pgserver.get_server(tmp_path_factory.mktemp("pg"), cleanup_mode="stop")
    yield server.get_uri()


@pytest.fixture
def conn(database_url):
    c = db.connect(database_url)
    c.execute("drop schema public cascade")
    c.execute("create schema public")
    c.commit()
    db.apply_migrations(c)
    yield c
    c.close()

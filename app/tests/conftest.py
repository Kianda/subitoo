"""Shared test fixtures: in-memory DB, fake adapters, and a recording sender."""

from __future__ import annotations

import pytest

from subitoo import registry
from subitoo.core import db
from tests._helpers import BoomSite, FakeSite, Recorder


@pytest.fixture(autouse=True)
def _register_fakes():
    registry.load_all()  # pull in real adapters too
    registry.SITES["fake"] = FakeSite
    registry.SITES["boom"] = BoomSite
    FakeSite.to_return = []
    yield
    registry.SITES.pop("fake", None)
    registry.SITES.pop("boom", None)


@pytest.fixture
def conn():
    c = db.init_db(":memory:")
    yield c
    c.close()


@pytest.fixture
def recorder():
    return Recorder()

"""
Tests for the base adapter datetime parsing utility.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.adapters.base import BaseAdapter


class _ConcreteAdapter(BaseAdapter):
    ats_name = "test"
    def fetch(self): return []


_adapter = _ConcreteAdapter("Test Co", "test-token")
_parse = _adapter._parse_dt


class TestParseDt:
    def test_iso8601_with_z(self):
        dt = _parse("2024-01-15T10:30:00Z")
        assert isinstance(dt, datetime)
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 15

    def test_iso8601_without_z(self):
        dt = _parse("2024-06-01T08:00:00")
        assert isinstance(dt, datetime)

    def test_epoch_ms(self):
        # 1705312000000 ms → ~2024-01-15
        dt = _parse(1705312000000)
        assert isinstance(dt, datetime)
        assert dt.year == 2024

    def test_epoch_s(self):
        dt = _parse(1705312000)
        assert isinstance(dt, datetime)
        assert dt.year == 2024

    def test_none_returns_none(self):
        assert _parse(None) is None

    def test_date_only_string(self):
        dt = _parse("2024-03-20")
        assert isinstance(dt, datetime)
        assert dt.year == 2024
        assert dt.month == 3
        assert dt.day == 20

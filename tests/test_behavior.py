# Copyright (C) 2010-2015 Cuckoo Foundation.
# This file is part of Cuckoo Sandbox - http://www.cuckoosandbox.org
# See the file 'docs/LICENSE' for copying permission.

import datetime
from pathlib import Path

from django.template import Context, Engine

from lib.cuckoo.common.config import Config
from modules.processing.behavior import ParseProcessLog

cfg = Config("processing")


class TestParseProcessLog:
    def test_init(self):
        assert (
            str(ParseProcessLog("CAPEv2/tests/test_bson.bson", cfg.behavior))
            == "<ParseProcessLog log-path: CAPEv2/tests/test_bson.bson>"
        )


CALL_METRICS = {
    "qpc_start": 1000,
    "qpc_end": 2500,
    "qpc_frequency": 1000000,
    "duration_us": 1500.0,
    "working_set_bytes": 1048576,
    "peak_working_set_bytes": 2097152,
    "private_usage_bytes": 524288,
    "pagefile_usage_bytes": 786432,
    "peak_pagefile_usage_bytes": 1572864,
}


def test_log_call_preserves_metrics():
    process = ParseProcessLog("missing.bson", cfg.behavior)
    process.first_seen = datetime.datetime(2026, 1, 1)

    process.log_call([20, 0, 1, 0, 7, 3, 4096, 8192, CALL_METRICS], "TestApi", "misc", [])

    assert process.lastcall["metrics"] == CALL_METRICS


def test_log_call_without_metrics_remains_compatible():
    process = ParseProcessLog("missing.bson", cfg.behavior)
    process.first_seen = datetime.datetime(2026, 1, 1)

    process.log_call([20, 0, 1, 0, 7, 3, 4096, 8192], "TestApi", "misc", [])

    assert "metrics" not in process.lastcall


def test_identical_calls_are_not_collapsed():
    process = ParseProcessLog("missing.bson", cfg.behavior)
    process.fd = object()
    calls = [
        {
            "api": "TestApi",
            "status": True,
            "arguments": [],
            "return": "0x00000000",
            "repeated": 0,
            "metrics": {"qpc_start": 1},
        },
        {
            "api": "TestApi",
            "status": True,
            "arguments": [],
            "return": "0x00000000",
            "repeated": 0,
            "metrics": {"qpc_start": 2},
        },
    ]

    class Parser:
        def read_next_message(self):
            if not calls:
                return False
            process.lastcall = calls.pop(0)
            return True

    process.parser = Parser()

    assert process.cacheless_next()["metrics"]["qpc_start"] == 1
    assert process.cacheless_next()["metrics"]["qpc_start"] == 2


def test_api_call_template_renders_metrics_and_missing_values():
    template_dir = Path(__file__).resolve().parents[1] / "web" / "templates"
    engine = Engine(dirs=[template_dir])
    template = engine.get_template("analysis/behavior/_api_call.html")
    chunk_template = engine.get_template("analysis/behavior/_chunk.html")
    base_call = {
        "timestamp": "00:00:00",
        "thread_id": "7",
        "caller": "0x00001000",
        "parentcaller": "0x00002000",
        "api": "TestApi",
        "arguments": [],
        "status": True,
        "return": "0x00000000",
        "repeated": 0,
    }

    rendered = template.render(Context({"call": {**base_call, "metrics": CALL_METRICS}}))
    missing = template.render(Context({"call": base_call}))
    headers = chunk_template.render(Context({"chunk": {"calls": []}}))

    assert "1500.000 µs" in rendered
    assert "1.0 MB" in rendered
    assert rendered.count("—") == 0
    assert missing.count("—") == 6
    for heading in ("Duration", "Working Set", "Peak Working Set", "Private Usage", "Pagefile Usage", "Peak Pagefile Usage"):
        assert heading in headers

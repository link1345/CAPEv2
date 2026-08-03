# Copyright (C) 2010-2015 Cuckoo Foundation.
# This file is part of Cuckoo Sandbox - http://www.cuckoosandbox.org
# See the file 'docs/LICENSE' for copying permission.

import datetime
import io
import pathlib
import struct

import pytest

from lib.cuckoo.common import netlog
from lib.cuckoo.common.netlog import BsonParser, CALL_METRIC_FIELDS

# Might require newer pymongo, works with 3.11.4


@pytest.fixture
def bson_file():
    class mock_handle:
        def __init__(self, filename):
            print(filename)
            self.file_handle = open(filename, "rb")
            self.process_log = ()

        def log_process(self, a, b, c, d, e, f):
            self.process_log = (a, b, c, d, e, f)

        def log_thread(self, a, b):
            pass

        def log_environ(self, a, b):
            pass

        def log_call(self, a, b, c, d):
            pass

        def read(self, num):
            return self.file_handle.read(num)

    yield mock_handle(pathlib.Path(__file__).absolute().parent.as_posix() + "/test_bson.bson")


class TestBsonParser:
    def test_init(self, bson_file):
        assert BsonParser(bson_file)

    def test_read_next_message(self, bson_file):
        b = BsonParser(bson_file)
        b.read_next_message()
        assert len(bson_file.process_log) == 0

        b.read_next_message()
        assert bson_file.process_log == (
            [0, 0, 1, 0, 2360, 0, 0, 0],
            datetime.datetime(2020, 11, 6, 9, 34, 36, 359375),
            1976,
            476,
            b"C:\\Windows\\sysnative\\lsass.exe",
            "lsass.exe",
        )


class TestCallMetrics:
    METRICS = {
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

    def _parse_call(self, monkeypatch, include_metrics):
        info = {"type": "info", "I": 20, "name": "TestApi", "category": "misc", "args": ["is_success", "retval"]}
        call = {"I": 20, "T": 7, "t": 3, "R": 4096, "P": 8192, "r": 0, "args": [1, 0]}
        if include_metrics:
            call.update(TestCallMetrics.METRICS)
        documents = {b"I": info, b"C": call}

        class FakeBson:
            def decode(self, data):
                return documents[data[4:5]]

        class Handle(io.BytesIO):
            def __init__(self):
                payload = b"".join(struct.pack("I", 5) + marker for marker in documents)
                super().__init__(payload)
                self.logged_call = None

            def log_call(self, context, apiname, category, arguments):
                self.logged_call = (context, apiname, category, arguments)

        monkeypatch.setattr(netlog, "bson", FakeBson(), raising=False)
        handle = Handle()
        parser = BsonParser(handle)
        parser.read_next_message()
        parser.read_next_message()
        return handle.logged_call

    def test_preserves_all_call_metrics(self, monkeypatch):
        context, apiname, category, arguments = self._parse_call(monkeypatch, include_metrics=True)

        assert apiname == "TestApi"
        assert category == "misc"
        assert arguments == []
        assert context[8] == self.METRICS
        assert tuple(context[8]) == CALL_METRIC_FIELDS
        assert context[8]["qpc_end"] >= context[8]["qpc_start"]
        assert context[8]["qpc_frequency"] > 0
        expected_duration = (context[8]["qpc_end"] - context[8]["qpc_start"]) * 1000000 / context[8]["qpc_frequency"]
        assert context[8]["duration_us"] == pytest.approx(expected_duration)

    def test_old_call_without_metrics_remains_compatible(self, monkeypatch):
        context, _, _, _ = self._parse_call(monkeypatch, include_metrics=False)

        assert context[8] == {}

# Copyright (C) 2010-2015 Cuckoo Foundation.
# This file is part of Cuckoo Sandbox - http://www.cuckoosandbox.org
# See the file 'docs/LICENSE' for copying permission.

import datetime
import json
import struct
from pathlib import Path

import bson
import pytest
from django.template import Context, Engine

from lib.cuckoo.common.config import Config
from lib.cuckoo.common.compressor import CuckooBsonCompressor
from lib.cuckoo.common.dictionary import Dictionary
from modules.processing.behavior import Enhanced, ParseProcessLog, Summary

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


@pytest.mark.parametrize("ram_boost", [False, True])
@pytest.mark.parametrize("ram_mmap", [False, True])
@pytest.mark.parametrize("loop_detection", [False, True])
def test_bson_metrics_survive_processing_and_iteration(tmp_path, ram_boost, ram_mmap, loop_detection):
    # Keep the real monitor's process metadata, then append measured and legacy calls.
    fixture = (Path(__file__).parent / "test_bson.bson").read_bytes()
    end = 0
    for _ in range(2):
        end += struct.unpack_from("<I", fixture, end)[0]
    info = {"type": "info", "I": 20, "name": "TestApi", "category": "misc", "args": ["is_success", "retval"]}
    call = {"I": 20, "T": 7, "t": 3, "R": 4096, "P": 8192, "r": 0, "args": [1, 0]}
    later_metrics = {**CALL_METRICS, "qpc_start": 3000, "qpc_end": 5500, "duration_us": 2500.0}
    documents = [info, {**call, **CALL_METRICS}, {**call, **later_metrics}, call]
    log_path = tmp_path / "metrics.bson"
    log_path.write_bytes(fixture[:end] + b"".join(bson.encode(document) for document in documents))
    if loop_detection:
        original = log_path.read_bytes()
        assert CuckooBsonCompressor().run(str(log_path), use_mmap=ram_mmap) is False
        assert log_path.read_bytes() == original
        assert not log_path.is_symlink()
    options = Dictionary({**cfg.behavior, "ram_boost": ram_boost, "ram_mmap": ram_mmap})
    process = ParseProcessLog(str(log_path), options)
    try:
        calls = list(process)
        assert len(calls) == 3
        assert [entry["id"] for entry in calls] == [0, 1, 2]
        assert calls[0]["metrics"] == CALL_METRICS
        assert calls[1]["metrics"] == later_metrics
        assert "metrics" not in calls[2]
        assert all(entry["repeated"] == 0 for entry in calls)
        assert json.loads(json.dumps(calls)) == calls
        assert list(process) == calls
    finally:
        process.close()


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
    assert "1.0 MB" in rendered.replace("\N{NO-BREAK SPACE}", " ")
    assert rendered.count("—") == 0
    assert missing.count("—") == 6
    zero = template.render(Context({"call": {**base_call, "metrics": {"duration_us": 0.0, "working_set_bytes": 0}}}))
    assert "0.000 µs" in zero
    assert "0 bytes" in zero.replace("\N{NO-BREAK SPACE}", " ")
    assert zero.count("—") == 4
    for heading in ("Duration", "Working Set", "Peak Working Set", "Private Usage", "Pagefile Usage", "Peak Pagefile Usage"):
        assert heading in headers


class TestSummaryAndEnhanced:
    def test_summary_read_files_and_file_activities(self):
        options = Dictionary({"replace_patterns": False, "file_activities": True})
        summary = Summary(options=options)
        process = {
            "process_id": 2256,
            "file_activities": {
                "read_files": [],
                "write_files": [],
                "delete_files": [],
            },
        }
        call = {
            "api": "NtOpenFile",
            "category": "filesystem",
            "status": True,
            "arguments": [
                {"name": "FileHandle", "value": "0x000002cc"},
                {"name": "DesiredAccess", "value": "0x00100021"},
                {"name": "FileName", "value": r"C:\Users\Bruno\AppData\Local\Temp\data.bin"},
                {"name": "ShareAccess", "value": "5"},
            ],
        }
        summary.event_apicall(call, process)
        result = summary.run()
        assert r"C:\Users\Bruno\AppData\Local\Temp\data.bin" in result["read_files"]
        assert r"C:\Users\Bruno\AppData\Local\Temp\data.bin" in process["file_activities"]["read_files"]

    def test_enhanced_registry_writes_and_disposition(self):
        enhanced = Enhanced()

        nt_set_call = {
            "api": "NtSetValueKey",
            "category": "registry",
            "timestamp": "2026-09-29 13:42:00,360",
            "arguments": [
                {
                    "name": "FullName",
                    "value": r"HKEY_CURRENT_USER\SOFTWARE\Microsoft\Windows\CurrentVersion\Internet Settings\5.0\Cache\Cookies\CachePrefix",
                },
                {"name": "Buffer", "value": "Cookie:"},
            ],
        }
        ev = enhanced._process_call(nt_set_call)
        assert ev is not None
        assert ev["event"] == "write"
        assert ev["object"] == "registry"
        assert ev["data"]["content"] == "Cookie:"

        # RegCreateKeyExW with Disposition=2 (REG_OPENED_EXISTING_KEY) should not be treated as a write
        open_existing_call = {
            "api": "RegCreateKeyExW",
            "category": "registry",
            "timestamp": "2026-09-29 13:42:00,400",
            "arguments": [
                {"name": "FullName", "value": r"HKEY_LOCAL_MACHINE\System\CurrentControlSet\Control\SecurityProviders\Schannel"},
                {"name": "Disposition", "value": "2"},
            ],
        }
        assert enhanced._process_call(open_existing_call) is None

        # RegCreateKeyExW with Disposition=1 (REG_CREATED_NEW_KEY) is recorded as a write
        create_new_call = {
            "api": "RegCreateKeyExW",
            "category": "registry",
            "timestamp": "2026-09-29 13:42:00,410",
            "arguments": [
                {"name": "FullName", "value": r"HKEY_CURRENT_USER\Software\NewMalwareKey"},
                {"name": "Disposition", "value": "1"},
            ],
        }
        ev_create = enhanced._process_call(create_new_call)
        assert ev_create is not None
        assert ev_create["event"] == "write"
        assert ev_create["data"]["regkey"] == r"HKEY_CURRENT_USER\Software\NewMalwareKey"

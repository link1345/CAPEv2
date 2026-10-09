import os
import threading
import unittest
from unittest.mock import MagicMock, mock_open, patch

from lib.api.process import Process


class ProcessTests(unittest.TestCase):
    def test_api_call_metrics_option_reaches_monitor(self):
        for value in (None, "0", "1"):
            with self.subTest(value=value):
                options = {} if value is None else {"api-call-metrics": value}
                process = Process(options=options, config=MagicMock(ip="127.0.0.1", port=2042), pid=1234)
                monitor_file = mock_open()
                with (
                    patch("lib.api.process.open", monitor_file),
                    patch("lib.api.process.LogServer"),
                    patch("lib.api.process.LOGSERVER_POOL", {}),
                    patch.object(Process, "process_num", 0),
                ):
                    process.write_monitor_config(interest="C:\\sample.exe")
                written = "".join(call.args[0] for call in monitor_file().write.call_args_list)
                if value is None:
                    self.assertNotIn("api-call-metrics=", written)
                else:
                    self.assertIn(f"api-call-metrics={value}\n", written)
                self.assertIn("host-ip=127.0.0.1\n", written)

    @patch("lib.api.process.PSAPI", MagicMock(), create=True)
    def test_unknown_image_name(self):
        process = Process()
        assert f"{process}" == "<Process 0 ???>"

    def test_known_image_name(self):
        mock_image_name = MagicMock()
        mock_image_name.return_value = self.id()
        with patch("lib.api.process.Process.get_image_name", mock_image_name):
            process = Process()
            assert f"{process}" == f"<Process 0 {self.id()}>"

    def test_process_self(self):
        _ = Process(pid=os.getpid(), thread_id=threading.get_ident())

    def test_process_fill_system_info(self):
        p = Process()
        p.fill_system_info()
        # arbitrary sysinfo field assertion here
        self.assertNotEqual(0, p.system_info.dwPageSize)

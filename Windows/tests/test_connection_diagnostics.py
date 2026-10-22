import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from iphonebackup import connection_diagnostics as diagnostics
from iphonebackup.usb import Library, NativeDependencyError


class ConnectionDiagnosticsTests(unittest.TestCase):
    def report(self, *, service_code=0, socket_error=None, library_error=None, custom=False):
        with patch.object(diagnostics.sys, 'platform', 'win32'), \
             patch.dict(diagnostics.os.environ, {'SystemRoot': 'C:/Windows',
                        'USBMUXD_SOCKET_ADDRESS': 'custom:1234' if custom else ''}), \
             patch.object(diagnostics.subprocess, 'run', return_value=SimpleNamespace(
                 returncode=service_code, stdout=b'STATE : 4 RUNNING', stderr=b'')) as run, \
             patch.object(diagnostics.socket, 'create_connection', side_effect=socket_error,
                          return_value=MagicMock()) as connect, \
             patch.object(diagnostics, 'Library') as library:
            library.return_value.devices.side_effect = library_error
            library.return_value.devices.return_value = ('private-device-identifier',)
            report = diagnostics.windows_report()
            self.assertEqual(run.call_args.args[0][1:], ['query', 'Apple Mobile Device Service'])
            self.assertEqual(run.call_args.kwargs['timeout'], 8)
            connect.assert_called_once_with(('127.0.0.1', 27015), timeout=2)
            self.assertNotIn('private-device-identifier', report)
            return report

    def test_running_service_and_device_count_do_not_claim_pairing(self):
        report = self.report()
        self.assertIn('4 RUNNING', report)
        self.assertIn('1 台 USB 裝置', report)
        self.assertIn('尚未確認 Apple 協定或手機', report)

    def test_closed_port_and_dll_failure_are_both_reported(self):
        report = self.report(socket_error=ConnectionRefusedError('refused'),
                             library_error=NativeDependencyError('DLL WinError 126'))
        self.assertIn('無法連線', report)
        self.assertIn('DLL WinError 126', report)

    def test_service_name_missing_is_not_proof_of_missing_installation(self):
        self.assertIn('不代表所有 Apple 裝置元件均未安裝', self.report(service_code=1060))

    def test_custom_endpoint_caveat(self):
        self.assertIn('不能代表自訂端點', self.report(custom=True))

    def test_service_timeout_does_not_hide_native_result(self):
        with patch.object(diagnostics.sys, 'platform', 'win32'), \
             patch.object(diagnostics.subprocess, 'run', side_effect=subprocess.TimeoutExpired('sc', 8)), \
             patch.object(diagnostics.socket, 'create_connection', return_value=MagicMock()), \
             patch.object(diagnostics, 'Library') as library:
            library.return_value.devices.return_value = ()
            report = diagnostics.windows_report()
        self.assertIn('服務查詢未完成', report)
        self.assertIn('0 台 USB 裝置', report)

    def test_native_load_failure_keeps_original_windows_error(self):
        with patch('iphonebackup.usb.c.CDLL', side_effect=OSError('WinError 126 dependency missing')), \
             patch('iphonebackup.usb.ctypes.util.find_library', return_value=None):
            with self.assertRaisesRegex(NativeDependencyError, 'WinError 126 dependency missing'):
                Library()

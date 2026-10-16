from threading import Event
import unittest
from unittest.mock import patch

from iphonebackup.models import DeviceFile
from iphonebackup.usb import DeviceError
from iphonebackup.voice_usb import AppAccessError, VoiceAppSession, scan_voice_apps


class AppSessionFixture:
    grants = {}
    attempts = []

    def __init__(self, udid, command):
        self.command = command

    def __enter__(self):
        self.attempts.append(self.command)
        result = self.grants.get(self.command)
        if isinstance(result, Exception):
            raise result
        return self

    def __exit__(self, *_):
        pass

    def walk(self, root, cancel):
        yield from self.grants.get(self.command, ())


class VoiceUSBTests(unittest.TestCase):
    def setUp(self):
        AppSessionFixture.grants = {}
        AppSessionFixture.attempts = []

    def test_both_interfaces_rejected_is_not_zero_recordings(self):
        AppSessionFixture.grants = {name: AppAccessError("ApplicationLookupFailed") for name in ("VendDocuments", "VendContainer")}
        with patch("iphonebackup.voice_usb.VoiceAppSession", AppSessionFixture):
            result = scan_voice_apps("device", Event(), lambda _: None, ["/Recordings: AFC 8"])
        self.assertEqual(result.availability, "unavailable")
        self.assertEqual(AppSessionFixture.attempts, ["VendDocuments", "VendContainer"])
        self.assertTrue(any("ApplicationLookupFailed" in text for text in result.warnings))
        self.assertTrue(any("不建立本機備份" in text for text in result.warnings))

    def test_granted_app_source_is_retained_for_later_export(self):
        AppSessionFixture.grants = {"VendDocuments": [DeviceFile("/recording.m4a", 100), DeviceFile("/layers.qta", 200)]}
        with patch("iphonebackup.voice_usb.VoiceAppSession", AppSessionFixture):
            result = scan_voice_apps("device", Event(), lambda _: None, [])
        self.assertEqual(len(result.items), 2)
        self.assertEqual(result.items[0].files[0].source, "VendDocuments")
        self.assertFalse(result.complete)
        self.assertEqual(AppSessionFixture.attempts, ["VendDocuments"])

    def test_empty_documents_does_not_prevent_container_check(self):
        AppSessionFixture.grants = {"VendDocuments": [], "VendContainer": [DeviceFile("/Documents/a.m4a", 12)]}
        with patch("iphonebackup.voice_usb.VoiceAppSession", AppSessionFixture):
            result = scan_voice_apps("device", Event(), lambda _: None, [])
        self.assertEqual(result.items[0].files[0].source, "VendContainer")

    def test_disconnect_is_not_hidden_as_access_restriction(self):
        AppSessionFixture.grants = {"VendDocuments": DeviceError("USB disconnected")}
        with patch("iphonebackup.voice_usb.VoiceAppSession", AppSessionFixture):
            with self.assertRaises(DeviceError):
                scan_voice_apps("device", Event(), lambda _: None, [])

    def test_cancel_does_not_open_another_interface(self):
        cancel = Event()
        cancel.set()
        with patch("iphonebackup.voice_usb.VoiceAppSession", AppSessionFixture):
            with self.assertRaises(DeviceError):
                scan_voice_apps("device", cancel, lambda _: None, [])
        self.assertEqual(AppSessionFixture.attempts, [])

    def test_only_known_read_requests_are_permitted(self):
        with self.assertRaises(ValueError):
            VoiceAppSession("device", "Delete")

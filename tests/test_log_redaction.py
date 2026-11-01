import asyncio
import io
import json
import logging
import unittest
from types import SimpleNamespace
from unittest.mock import mock_open, patch

from iSponsorBlockTV.debug_helpers import AiohttpTracer
from iSponsorBlockTV.log_redaction import SecretRedactingFormatter, register_secrets


class LogRedactionTests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        self.handler = logging.StreamHandler(self.output)
        self.handler.setFormatter(SecretRedactingFormatter("%(levelname)s %(message)s"))
        self.logger = logging.Logger("security-test", logging.DEBUG)
        self.logger.addHandler(self.handler)

    def assert_secret_removed(self, secret):
        text = self.output.getvalue()
        self.assertNotIn(secret, text)
        self.assertIn("[REDACTED]", text)

    def test_dependency_info_messages_hide_lounge_tokens(self):
        self.logger.info("Refreshed auth, lounge id token %s", "refresh-secret")
        self.logger.info("Subscribing to lounge id %s", "subscribe-secret")
        self.assert_secret_removed("refresh-secret")
        self.assert_secret_removed("subscribe-secret")
        self.assertIn("Subscribing to lounge id", self.output.getvalue())

    def test_nested_lounge_event_keeps_diagnostics_without_encryption_key(self):
        event = [
            {"devices": json.dumps([{"name": "TV", "localChannelEncryptionKey": "key-secret"}])}
        ]
        self.logger.debug("process_event(loungeStatus, %s)", event)
        self.assert_secret_removed("key-secret")
        self.assertIn("loungeStatus", self.output.getvalue())
        self.assertIn("TV", self.output.getvalue())

    def test_escaped_json_redacts_multiple_secret_fields(self):
        event = json.dumps(
            {"auth": json.dumps({"loungeIdToken": "token-secret", "refresh_token": "other-secret"})}
        )
        self.logger.debug("Event %s", event)
        self.assert_secret_removed("token-secret")
        self.assert_secret_removed("other-secret")

    def test_url_query_credentials_are_removed(self):
        self.logger.debug(
            "GET https://www.googleapis.com/videos?key=api-secret%2Bvalue&id=video-id"
            "&loungeIdToken=lounge-secret&part=snippet"
        )
        self.assert_secret_removed("api-secret")
        self.assert_secret_removed("lounge-secret")
        self.assertIn("id=video-id", self.output.getvalue())
        self.assertIn("part=snippet", self.output.getvalue())

    def test_quoted_credentials_with_spaces_are_removed(self):
        self.logger.debug(
            "Auth %s", {"password": "a secret with spaces", "Authorization": "Bearer auth-secret"}
        )
        self.assert_secret_removed("a secret with spaces")
        self.assert_secret_removed("auth-secret")

    def test_plain_authorization_header_is_removed(self):
        self.logger.debug("Authorization: Bearer header-secret")
        self.assert_secret_removed("header-secret")

    def test_cookie_header_does_not_leak_additional_cookies(self):
        self.logger.debug("Cookie: SID=first-secret; other=second-secret")
        self.assert_secret_removed("first-secret")
        self.assert_secret_removed("second-secret")

    def test_screen_id_fields_and_warning_messages_are_redacted(self):
        screen_id = "private-screen-id-12345678901234567890"
        self.logger.debug("Device %s", {"screenId": screen_id})
        self.logger.warning("The screen id %s uses the old format", screen_id)
        self.assert_secret_removed(screen_id)
        self.assertIn("uses the old format", self.output.getvalue())

    def test_device_logger_name_uses_stable_label_without_changing_record(self):
        screen_id = "private-screen-id-12345678901234567890"
        record = logging.LogRecord(
            f"iSponsorBlockTV-{screen_id}", logging.INFO, "", 0, "Connected", (), None
        )
        formatter = SecretRedactingFormatter("%(name)s %(message)s")
        first = formatter.format(record)
        self.assertNotIn(screen_id, first)
        self.assertEqual(first, formatter.format(record))
        self.assertRegex(first, r"^iSponsorBlockTV-device-[0-9a-f]{8} Connected$")
        self.assertEqual(record.name, f"iSponsorBlockTV-{screen_id}")

    def test_configured_credentials_under_generic_fields_are_removed(self):
        screen_id = "configured-device-identifier-1234567890"
        api_key = "configured-api-credential-1234567890"
        register_secrets([screen_id, api_key, "", None])
        self.logger.debug("Device %s response %s", {"id": screen_id, "state": 1}, api_key)
        self.assert_secret_removed(screen_id)
        self.assert_secret_removed(api_key)
        self.assertIn("'state': 1", self.output.getvalue())

    def test_configuration_load_registers_credentials_before_logging(self):
        from iSponsorBlockTV.helpers import Config

        screen_id = "loaded-device-identifier-1234567890"
        api_key = "loaded-api-credential-1234567890"
        config = json.dumps({"devices": [{"screen_id": screen_id}], "apikey": api_key})
        with patch("builtins.open", mock_open(read_data=config)):
            Config("/unused-config")
        self.logger.info("Loaded device %s and credential %s", screen_id, api_key)
        self.assert_secret_removed(screen_id)
        self.assert_secret_removed(api_key)

    def test_quoted_password_escapes_do_not_leak_suffix(self):
        secret = "prefix'and\"suffix"
        self.logger.debug("Auth %s", {"password": secret, "state": 1})
        self.assert_secret_removed("prefix")
        self.assert_secret_removed("suffix")
        self.assertIn("'state': 1", self.output.getvalue())

    def test_nested_json_password_escapes_do_not_leak_suffix(self):
        event = json.dumps({"auth": json.dumps({"password": 'prefix"suffix', "state": 1})})
        self.logger.debug("Auth %s", event)
        self.assert_secret_removed("prefix")
        self.assert_secret_removed("suffix")
        self.assertIn("state", self.output.getvalue())

    def test_pairing_log_does_not_expose_code(self):
        self.logger.debug("Launching YouTube app on TV with pairing code %s", "123456789012")
        self.assert_secret_removed("123456789012")

    def test_exception_text_is_redacted(self):
        try:
            raise ValueError("request failed: loungeIdToken=exception-secret&state=1")
        except ValueError:
            self.logger.exception("Request failed")
        self.assert_secret_removed("exception-secret")
        self.assertIn("ValueError", self.output.getvalue())
        self.assertIn("state=1", self.output.getvalue())

    def test_ordinary_playback_diagnostics_are_preserved(self):
        self.logger.info("Scheduling pre-end return for video-id in %.1fs (state %s)", 5.0, 1)
        self.assertEqual(
            self.output.getvalue(),
            "INFO Scheduling pre-end return for video-id in 5.0s (state 1)\n",
        )

    def test_http_trace_logs_size_without_text_or_binary_payloads(self):
        tracer = AiohttpTracer(self.logger)
        for chunk in (b"unstructured-secret", b"\xffbinary-secret"):
            asyncio.run(
                tracer.on_response_chunk_received(None, object(), SimpleNamespace(chunk=chunk))
            )
            self.assertNotIn(chunk.hex(), self.output.getvalue())
        self.assertNotIn("secret", self.output.getvalue())
        self.assertIn("bytes", self.output.getvalue())


if __name__ == "__main__":
    unittest.main()

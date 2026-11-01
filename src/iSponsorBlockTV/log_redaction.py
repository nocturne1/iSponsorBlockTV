"""Remove credentials from application and dependency log output."""

import copy
import hashlib
import logging
import re


_SECRET_KEYS = (
    r"lounge[_ -]?id[_ -]?token|lounge[_ -]?token|refresh[_ -]?token|"
    r"access[_ -]?token|id[_ -]?token|local[_ -]?channel[_ -]?encryption[_ -]?key|"
    r"api[_ -]?key|key|password|client[_ -]?secret|pairing[_ -]?code|screen[_ -]?id|"
    r"(?:proxy-)?authorization|cookie|set-cookie"
)
_PREFIX = rf"(?P<prefix>\b(?:{_SECRET_KEYS})(?:\\?[\"'])?\s*[:=]\s*)"
_ESCAPED_QUOTED_VALUE = re.compile(
    _PREFIX + r"(?P<quote>\\[\"'])(?P<value>.*?)(?<!\\)(?P=quote)",
    re.IGNORECASE | re.DOTALL,
)
_QUOTED_VALUE = re.compile(
    _PREFIX + r"(?P<quote>[\"'])(?P<value>(?:\\.|(?!(?P=quote))[^\\])*)(?P=quote)",
    re.IGNORECASE | re.DOTALL,
)
_UNQUOTED_VALUE = re.compile(_PREFIX + r"(?P<value>[^\s&,;\[\]{}<>\"'\\]+)", re.IGNORECASE)
_AUTHORIZATION = re.compile(
    r"(?P<prefix>\b(?:proxy-)?authorization\s*[:=]\s*)"
    r"(?:Bearer|Basic)\s+[^\s,;\"']+",
    re.IGNORECASE,
)
_TOKEN_MESSAGE = re.compile(
    r"(?P<prefix>\b(?:Refreshed auth, lounge id token|Subscribing to lounge id)\s+)\S+",
    re.IGNORECASE,
)
_PAIRING_MESSAGE = re.compile(r"(?P<prefix>\bpairing code\s+)\d{12}\b", re.IGNORECASE)
_SCREEN_MESSAGE = re.compile(r"(?P<prefix>\bscreen[_ -]?id\s+)[a-zA-Z0-9_-]{26,}\b", re.IGNORECASE)
_COOKIE_HEADER = re.compile(
    r"(?P<prefix>(?<![\w\"'\\])(?:set-cookie|cookie)\s*:\s*)[^\r\n]+", re.IGNORECASE
)


class SecretRedactingFormatter(logging.Formatter):
    def format(self, record):
        if record.name.startswith("iSponsorBlockTV-"):
            record = copy.copy(record)
            device_id = record.name.removeprefix("iSponsorBlockTV-")
            device_label = hashlib.sha256(device_id.encode()).hexdigest()[:8]
            record.name = f"iSponsorBlockTV-device-{device_label}"
        # Redact after formatting so exception text and dependency log arguments are covered too.
        message = super().format(record)
        for pattern in (
            _TOKEN_MESSAGE,
            _PAIRING_MESSAGE,
            _SCREEN_MESSAGE,
            _AUTHORIZATION,
            _COOKIE_HEADER,
        ):
            message = pattern.sub(lambda match: match["prefix"] + "[REDACTED]", message)
        for pattern in (_ESCAPED_QUOTED_VALUE, _QUOTED_VALUE):
            message = pattern.sub(
                lambda match: match["prefix"] + match["quote"] + "[REDACTED]" + match["quote"],
                message,
            )
        return _UNQUOTED_VALUE.sub(lambda match: match["prefix"] + "[REDACTED]", message)

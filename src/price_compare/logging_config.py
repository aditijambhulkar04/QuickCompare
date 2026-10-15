"""Central logging setup.

Both entry points (api.py, cli.py) call configure_logging() once at
startup, rather than every module configuring logging ad hoc. Every other
module just does `logger = logging.getLogger(__name__)` and logs through
that -- the level and format are controlled from here alone.
"""

import logging
import os

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_NOISY_THIRD_PARTY_LOGGERS = ("litellm", "httpx", "httpcore")


def configure_logging() -> None:
    log_level_name = os.environ.get("PRICE_COMPARE_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=log_level_name, format=_LOG_FORMAT)

    if log_level_name != "DEBUG":
        for logger_name in _NOISY_THIRD_PARTY_LOGGERS:
            logging.getLogger(logger_name).setLevel(logging.WARNING)

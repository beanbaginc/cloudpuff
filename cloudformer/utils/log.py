from __future__ import unicode_literals


import logging


class LogLevelFilter(logging.Filter):
    """Filter log messages of a given level.

    Only log messages that have the specified level will be allowed by
    this filter. This prevents propagation of higher level types to lower
    log handlers.
    """
    def __init__(self, level):
        self.level = level

    def filter(self, record):
        return record.levelno == self.level


def init_logging(debug=False):
    root = logging.getLogger()

    if debug:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter('>>> [%(name)s] %(message)s'))
        handler.setLevel(logging.DEBUG)
        handler.addFilter(LogLevelFilter(logging.DEBUG))
        root.addHandler(handler)

        root.setLevel(logging.DEBUG)
    else:
        root.setLevel(logging.INFO)

    # Handler for info messages. We'll treat these like prints.
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(message)s'))
    handler.setLevel(logging.INFO)
    handler.addFilter(LogLevelFilter(logging.INFO))
    root.addHandler(handler)

    # Handler for warnings, errors, and criticals. They'll show the
    # level prefix and the message.
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        '[%(name)s] %(levelname)s: %(message)s'))
    handler.setLevel(logging.WARNING)
    root.addHandler(handler)

    # Disable all non-critical errors from boto. We want to catch them and
    # handle them ourselves.
    logging.getLogger('boto').setLevel(logging.CRITICAL)

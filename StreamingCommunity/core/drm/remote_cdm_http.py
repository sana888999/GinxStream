# 09.02.26
"""Temporary HTTP defaults while pywidevine/pyplayready RemoteCdm init calls requests with no timeout."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator


@contextmanager
def remote_cdm_init_timeouts(connect: float = 8.0, read: float = 45.0) -> Iterator[None]:
    """
    RemoteCdm constructors call requests.head(self.host) with no timeout; slow networks
    may still fail, but this avoids instant failures on marginal links.
    """
    import requests
    from requests.sessions import Session

    _orig_head = requests.head
    _orig_request = Session.request

    def head(url, **kwargs):  # noqa: ANN001
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = (connect, read)
        return _orig_head(url, **kwargs)

    def request(self, method, url, **kwargs):  # noqa: ANN001
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = (connect, read)
        return _orig_request(self, method, url, **kwargs)

    requests.head = head
    Session.request = request
    try:
        yield
    finally:
        requests.head = _orig_head
        Session.request = _orig_request

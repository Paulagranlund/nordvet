"""
Tests for the API client, using a fake API instead of the real one.

The fake returns prepared responses, so we can check how the client
handles pagination, rate limits and errors without any network calls.
"""

import pytest

from pipeline import api_client
from pipeline.api_client import VetOneClient


class FakeResponse:
    def __init__(self, status_code, body=None, headers=None):
        self.status_code = status_code
        self._body = body or {}
        self.headers = headers or {}
        self.text = str(body)

    def json(self):
        return self._body

    def raise_for_status(self):
        pass


class FakeSession:
    """Returns the prepared GET responses in order, and records each request."""

    def __init__(self, get_responses):
        self.get_responses = list(get_responses)
        self.get_calls = []

    def post(self, url, json, timeout):  # login
        return FakeResponse(200, {"access_token": "test-token"})

    def get(self, url, params, headers, timeout):
        self.get_calls.append(dict(params or {}))
        return self.get_responses.pop(0)


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    """Skip all sleeping (throttling and retry waits) so tests run instantly."""
    monkeypatch.setattr(api_client.time, "sleep", lambda seconds: None)


def make_client(responses):
    client = VetOneClient(base_url="https://fake", client_id="id", client_secret="secret")
    client.session = FakeSession(responses)
    return client


def test_pagination_follows_next_cursor():
    client = make_client([
        FakeResponse(200, {"data": [{"id": "a1"}, {"id": "a2"}], "next_cursor": "page2"}),
        FakeResponse(200, {"data": [{"id": "a3"}], "next_cursor": None}),
    ])
    pages = list(client.iter_appointments(include_deleted="true"))
    assert pages == [[{"id": "a1"}, {"id": "a2"}], [{"id": "a3"}]]
    # the second request must send the cursor from the first response
    assert client.session.get_calls[1]["cursor"] == "page2"


def test_rate_limit_and_server_error_are_retried():
    client = make_client([
        FakeResponse(429, headers={"Retry-After": "3"}),
        FakeResponse(500),
        FakeResponse(200, {"data": [{"id": "a1"}]}),
    ])
    assert client.get_clinics() == [{"id": "a1"}]
    assert len(client.session.get_calls) == 3


def test_bad_request_fails_immediately():
    # A 400 means our request is wrong: retrying would only hide a bug
    client = make_client([FakeResponse(400, {"error": "invalid from"})])
    with pytest.raises(RuntimeError, match="400"):
        client.get_clinics()
    assert len(client.session.get_calls) == 1

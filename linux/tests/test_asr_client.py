"""Tests for ASR client URL building and message parsing."""

import asyncio
import contextlib
import json
import builtins
import threading
from types import SimpleNamespace

import pytest

from doubao_murmur import asr_client as asr_client_module
from doubao_murmur.asr_client import (
    ASRClient,
    _load_websockets,
    _is_connection_closed_error,
    _websocket_header_kwargs,
    is_handshake_auth_rejection,
)
from doubao_murmur.params_store import ASRParams


@pytest.fixture
def client():
    return ASRClient()


@pytest.fixture
def sample_params():
    return ASRParams(
        cookies={"sessionid": "abc", "sid_tt": "xyz"},
        device_id="device_12345",
        web_id="web_67890",
    )


class TestURLBuilding:
    def test_url_contains_base(self, client, sample_params):
        url = client._build_url(sample_params)
        assert url.startswith("wss://ws-samantha.doubao.com/samantha/audio/asr?")

    def test_url_contains_device_id(self, client, sample_params):
        url = client._build_url(sample_params)
        assert "device_id=device_12345" in url

    def test_url_contains_web_id(self, client, sample_params):
        url = client._build_url(sample_params)
        assert "web_id=web_67890" in url

    def test_url_contains_tea_uuid(self, client, sample_params):
        url = client._build_url(sample_params)
        assert "tea_uuid=web_67890" in url

    def test_url_contains_fixed_params(self, client, sample_params):
        url = client._build_url(sample_params)
        assert "version_code=20800" in url
        assert "language=zh" in url
        assert "aid=497858" in url
        assert "format=pcm" in url

    def test_url_contains_unique_tab_id(self, client, sample_params):
        url1 = client._build_url(sample_params)
        url2 = client._build_url(sample_params)
        # web_tab_id should be different each time (UUID)
        assert url1 != url2


class TestMessageParsing:
    def test_result_event(self, client):
        received = []
        client.on_result = lambda text: received.append(text)

        msg = json.dumps({
            "code": 0,
            "event": "result",
            "result": {"Text": "你好世界"},
        })
        client._handle_message(msg)
        assert received == ["你好世界"]

    def test_finish_event(self, client):
        finished = []
        client.on_finish = lambda: finished.append(True)

        msg = json.dumps({"code": 0, "event": "finish"})
        client._handle_message(msg)
        assert finished == [True]

    def test_auth_error_code(self, client):
        auth_errors = []
        client.on_auth_error = lambda: auth_errors.append(True)

        msg = json.dumps({
            "code": 709599054,
            "event": "",
            "message": "Invalid",
        })
        client._handle_message(msg)
        assert auth_errors == [True]

    def test_auth_error_keyword_cookie(self, client):
        auth_errors = []
        client.on_auth_error = lambda: auth_errors.append(True)

        msg = json.dumps({
            "code": 12345,
            "event": "",
            "message": "Cookie expired",
        })
        client._handle_message(msg)
        assert auth_errors == [True]

    def test_auth_error_keyword_session(self, client):
        auth_errors = []
        client.on_auth_error = lambda: auth_errors.append(True)

        msg = json.dumps({
            "code": 99,
            "event": "",
            "message": "session unauthorized",
        })
        client._handle_message(msg)
        assert auth_errors == [True]

    def test_auth_error_tourist_limit(self, client):
        # Sent once right before the server drops an expired login.
        auth_errors = []
        client.on_auth_error = lambda: auth_errors.append(True)

        msg = json.dumps({
            "event": "",
            "result": None,
            "code": 710022013,
            "message": "tourist reach limited",
        })
        client._handle_message(msg)
        assert auth_errors == [True]

    def test_non_auth_error_ignored(self, client):
        errors = []
        client.on_error = lambda e: errors.append(e)

        msg = json.dumps({
            "code": 1,
            "event": "",
            "message": "some other error",
        })
        client._handle_message(msg)
        assert errors == []  # non-auth errors with code != 0 are silently ignored

    def test_empty_text_ignored(self, client):
        received = []
        client.on_result = lambda text: received.append(text)

        msg = json.dumps({
            "code": 0,
            "event": "result",
            "result": {"Text": ""},
        })
        client._handle_message(msg)
        assert received == []

    def test_binary_message(self, client):
        received = []
        client.on_result = lambda text: received.append(text)

        msg = json.dumps({
            "code": 0,
            "event": "result",
            "result": {"Text": "test"},
        }).encode("utf-8")
        client._handle_message(msg)
        assert received == ["test"]

    def test_invalid_json_ignored(self, client):
        received = []
        client.on_result = lambda text: received.append(text)

        client._handle_message("not json at all")
        assert received == []


class TestHandshakeAuthRejection:
    @staticmethod
    def invalid_status(status_code, body=b""):
        """Shape of websockets>=14 InvalidStatus."""
        return SimpleNamespace(
            response=SimpleNamespace(status_code=status_code, body=body)
        )

    def test_http_401_and_403(self):
        assert is_handshake_auth_rejection(self.invalid_status(401))
        assert is_handshake_auth_rejection(self.invalid_status(403))

    def test_legacy_status_code_attribute(self):
        assert is_handshake_auth_rejection(SimpleNamespace(status_code=403))

    def test_http_200_with_login_invalid_body(self):
        body = json.dumps({
            "code": 710012001,
            "message": "login invalid",
            "msg": "登录已过期，请重新登录",
        }).encode()
        assert is_handshake_auth_rejection(self.invalid_status(200, body))

    def test_http_200_with_unrelated_body(self):
        body = json.dumps({"code": 1, "message": "rate limited"}).encode()
        assert not is_handshake_auth_rejection(self.invalid_status(200, body))

    def test_http_200_with_non_json_body(self):
        assert not is_handshake_auth_rejection(
            self.invalid_status(200, b"<html>captive portal</html>")
        )

    def test_network_errors(self):
        assert not is_handshake_auth_rejection(TimeoutError())
        assert not is_handshake_auth_rejection(OSError("connection reset"))
        assert not is_handshake_auth_rejection(self.invalid_status(502))


@contextlib.contextmanager
def local_asr_server(handler, process_request=None):
    """Serve `handler` on a loopback port from a background event loop."""
    serve = pytest.importorskip("websockets.asyncio.server").serve
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    async def start():
        return await serve(
            handler, "127.0.0.1", 0, process_request=process_request
        )

    async def stop(server):
        server.close()
        await server.wait_closed()

    server = asyncio.run_coroutine_threadsafe(start(), loop).result(5)
    try:
        yield "ws://127.0.0.1:%d" % server.sockets[0].getsockname()[1]
    finally:
        asyncio.run_coroutine_threadsafe(stop(server), loop).result(5)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
        loop.close()


class TestServerClose:
    """Drive the real receive loop against a local server."""

    @pytest.fixture
    def recorder(self, client, monkeypatch):
        fired = []
        opened = threading.Event()
        client.on_open = lambda: (fired.append("open"), opened.set())
        client.on_result = lambda text: fired.append(("result", text))
        client.on_error = lambda error: fired.append(("error", error))
        client.on_auth_error = lambda: fired.append("auth_error")

        def run(url, params, after_open=None):
            monkeypatch.setattr(asr_client_module, "WSS_BASE_URL", url)
            client.connect(params)
            if after_open:
                assert opened.wait(5)
                after_open()
            client._thread.join(5)
            assert not client._thread.is_alive()
            return fired

        return run

    def test_close_before_any_message_is_auth_error(self, recorder, sample_params):
        # What the server does to an expired login (issue #21).
        async def handler(ws):
            await ws.close(1000, "1000-")

        with local_asr_server(handler) as url:
            fired = recorder(url, sample_params)
        assert fired == ["open", "auth_error"]

    def test_close_after_results_is_not_auth_error(self, recorder, sample_params):
        # The server also ends long valid sessions with 1000 "1000-".
        async def handler(ws):
            await ws.send(json.dumps(
                {"code": 0, "event": "result", "result": {"Text": "你好"}}
            ))
            await ws.close(1000, "1000-")

        with local_asr_server(handler) as url:
            fired = recorder(url, sample_params)
        assert fired == ["open", ("result", "你好")]

    def test_tourist_limit_then_close_reports_auth_once(
        self, recorder, sample_params
    ):
        async def handler(ws):
            await ws.send(json.dumps({
                "event": "", "result": None,
                "code": 710022013, "message": "tourist reach limited",
            }))
            await ws.close(1000, "1000-")

        with local_asr_server(handler) as url:
            fired = recorder(url, sample_params)
        assert fired == ["open", "auth_error"]

    def test_our_own_disconnect_is_not_auth_error(
        self, client, recorder, sample_params
    ):
        async def handler(ws):
            await ws.wait_closed()

        with local_asr_server(handler) as url:
            fired = recorder(url, sample_params, after_open=client.disconnect)
        assert fired == ["open"]

    def test_abnormal_close_is_a_connection_error(self, recorder, sample_params):
        async def handler(ws):
            await ws.close(1011, "internal error")

        with local_asr_server(handler) as url:
            fired = recorder(url, sample_params)
        assert fired[0] == "open"
        assert [kind for kind, _ in fired[1:]] == ["error"]

    def test_login_invalid_handshake_is_an_auth_rejection(
        self, recorder, sample_params
    ):
        from http import HTTPStatus

        def process_request(connection, request):
            return connection.respond(HTTPStatus.OK, json.dumps(
                {"code": 710012001, "message": "login invalid"}
            ))

        async def handler(ws):
            pass

        with local_asr_server(handler, process_request) as url:
            fired = recorder(url, sample_params)
        assert [kind for kind, _ in fired] == ["error"]
        assert is_handshake_auth_rejection(fired[0][1])


class TestCookieHeader:
    def test_cookie_header_format(self):
        params = ASRParams(
            cookies={"sessionid": "abc", "sid_tt": "xyz"},
            device_id="d",
            web_id="w",
        )
        header = params.cookie_header
        # Should contain both cookies separated by "; "
        assert "sessionid=abc" in header
        assert "sid_tt=xyz" in header


class TestDependencyHandling:
    def test_header_kwargs_new_websockets(self):
        def connect(*, additional_headers=None):
            return additional_headers

        assert _websocket_header_kwargs(connect, {"Cookie": "x"}) == {
            "additional_headers": {"Cookie": "x"}
        }

    def test_header_kwargs_old_websockets(self):
        def connect(*, extra_headers=None):
            return extra_headers

        assert _websocket_header_kwargs(connect, {"Cookie": "x"}) == {
            "extra_headers": {"Cookie": "x"}
        }

    def test_load_websockets_error_is_actionable(self, monkeypatch):
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "websockets":
                raise ImportError("missing")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        with pytest.raises(RuntimeError, match="websockets"):
            _load_websockets()

    def test_connection_closed_error_detection(self):
        class ConnectionClosedOK(Exception):
            pass

        assert _is_connection_closed_error(ConnectionClosedOK())
        assert not _is_connection_closed_error(RuntimeError())

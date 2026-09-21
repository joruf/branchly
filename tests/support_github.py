"""
A fake GitHub for the API tests.

A real HTTP server on localhost rather than a stubbed ``requests.Session``,
because the parts of the transport worth testing are exactly the parts a stub
would paper over: pagination through the ``Link`` header, conditional requests
answered with 304, rate-limit headers, and how a 422 body becomes a sentence.

Routes are registered per method and path. A route is either a fixed response or
a callable that gets the parsed body, which is what lets a test assert that a
merge really sent ``merge_method: squash``.
"""

from __future__ import annotations

import json
import socketserver
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse


@dataclass(slots=True)
class RecordedRequest:
    """
    One request the fake server received.

    Attributes:
        method: HTTP verb.
        path: Path without the query string.
        query: Parsed query parameters, each value a list.
        body: Parsed JSON body, or the raw bytes when it was not JSON.
        headers: Request headers.
    """

    method: str
    path: str
    query: dict[str, list[str]] = field(default_factory=dict)
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class Response:
    """
    What a route answers with.

    Attributes:
        status: HTTP status code.
        body: Object to serialise as JSON, or None for an empty body.
        headers: Extra response headers.
    """

    status: int = 200
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)


Route = Callable[[RecordedRequest], Response]


class _QuietServer(ThreadingHTTPServer):
    """
    A server that does not look its own name up.

    ``HTTPServer.server_bind`` calls ``socket.getfqdn`` on the bound address,
    which is a reverse DNS lookup. On a machine whose resolver has no answer for
    127.0.0.1 that costs a timeout per test, and the suite spends most of its
    time waiting for a name nothing reads.
    """

    # Shutting down otherwise waits for every keep-alive connection to time out.
    daemon_threads = True

    def server_bind(self) -> None:
        """
        Binds the socket without resolving the host name.

        Returns:
            None
        """

        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


class FakeGitHub:
    """
    A throwaway HTTP server standing in for api.github.com.

    Attributes:
        requests: Every request received, in order.
    """

    def __init__(self) -> None:
        """
        Starts the server on a free port.
        """

        self.requests: list[RecordedRequest] = []
        self._routes: dict[tuple[str, str], Route] = {}
        self._lock = threading.Lock()

        fake = self

        class Handler(BaseHTTPRequestHandler):
            """
            Dispatches one request onto the registered routes.
            """

            protocol_version = "HTTP/1.1"
            # Without this every small response waits on a delayed ACK, which
            # costs more than the request itself on a loopback connection.
            disable_nagle_algorithm = True

            def log_message(self, *_args: Any) -> None:
                """
                Silences the default logging to stderr.

                Returns:
                    None
                """

            def _handle(self) -> None:
                """
                Answers one request.

                Returns:
                    None
                """

                parsed = urlparse(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw) if raw else None
                except ValueError:
                    body = raw
                record = RecordedRequest(
                    method=self.command,
                    path=parsed.path,
                    query=parse_qs(parsed.query),
                    body=body,
                    headers={key.lower(): value for key, value in self.headers.items()},
                )
                response = fake._dispatch(record)

                payload = b"" if response.body is None else json.dumps(response.body).encode()
                self.send_response(response.status)
                if payload:
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                for key, value in response.headers.items():
                    self.send_header(key, value)
                self.end_headers()
                if payload:
                    self.wfile.write(payload)

            # BaseHTTPRequestHandler dispatches on these exact names.
            do_GET = _handle  # noqa: N815
            do_POST = _handle  # noqa: N815
            do_PATCH = _handle  # noqa: N815
            do_PUT = _handle  # noqa: N815
            do_DELETE = _handle  # noqa: N815

        self._server = _QuietServer(("127.0.0.1", 0), Handler)
        # The default poll interval is half a second, and shutting down waits
        # for one full tick of it. Multiplied by a test each, that is most of
        # the suite's runtime.
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self._thread.start()

    @property
    def root(self) -> str:
        """
        Returns the base URL to hand the client.

        Returns:
            str: ``http://127.0.0.1:<port>``.
        """

        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def stop(self) -> None:
        """
        Shuts the server down.

        Returns:
            None
        """

        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    # ------------------------------------------------------------------ routes

    def route(self, method: str, path: str, handler: Route) -> None:
        """
        Registers a handler for one method and path.

        Args:
            method: HTTP verb.
            path: Exact path, for example ``/repos/o/r/issues``.
            handler: Callable answering the request.

        Returns:
            None
        """

        self._routes[(method.upper(), path)] = handler

    def json(self, method: str, path: str, body: Any, status: int = 200, **headers: str) -> None:
        """
        Registers a fixed JSON response.

        Args:
            method: HTTP verb.
            path: Exact path.
            body: Object to return.
            status: Status code.
            **headers: Extra response headers, underscores becoming hyphens.

        Returns:
            None
        """

        clean = {key.replace("_", "-"): value for key, value in headers.items()}
        self.route(method, path, lambda _request: Response(status=status, body=body, headers=clean))

    def pages(self, method: str, path: str, pages: list[list[Any]]) -> None:
        """
        Registers a paginated list, one entry per page.

        The ``Link`` header points at this same server, which is what the
        transport is supposed to follow.

        Args:
            method: HTTP verb.
            path: Exact path.
            pages: The pages, in order.

        Returns:
            None
        """

        def handler(request: RecordedRequest) -> Response:
            try:
                index = int(request.query.get("page", ["1"])[0]) - 1
            except ValueError:
                index = 0
            if index < 0 or index >= len(pages):
                return Response(body=[])
            headers: dict[str, str] = {}
            if index + 1 < len(pages):
                headers["Link"] = f'<{self.root}{path}?page={index + 2}>; rel="next"'
            return Response(body=pages[index], headers=headers)

        self.route(method, path, handler)

    def _dispatch(self, record: RecordedRequest) -> Response:
        """
        Finds the handler for a request and records it.

        Args:
            record: The request.

        Returns:
            Response: What the route answered, or a 404.
        """

        with self._lock:
            self.requests.append(record)
            handler = self._routes.get((record.method, record.path))
        if handler is None:
            return Response(status=404, body={"message": "Not Found"})
        return handler(record)

    def calls(self, method: str, path: str) -> list[RecordedRequest]:
        """
        Returns the recorded requests matching a method and path.

        Args:
            method: HTTP verb.
            path: Exact path.

        Returns:
            list[RecordedRequest]: Matching requests, in order.
        """

        with self._lock:
            return [
                item
                for item in self.requests
                if item.method == method.upper() and item.path == path
            ]

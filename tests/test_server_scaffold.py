"""Offline checks for the shared application and its startup boundary."""
import importlib
import logging
import runpy
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from mcp import Client

ROOT = Path(__file__).resolve().parents[1]

# Tools listed in spec.md section 3. Each tool module registers itself on the
# shared `mcp`; the scaffold test only asserts nothing else has crept in.
EXPECTED_TOOLS = frozenset(
    {
        "search_airports",
        "search_flights",
        "search_multi_city",
        "find_cheapest_dates",
        "get_price_insight",
    }
)


@pytest.fixture
def app():
    return importlib.import_module("fastflights_mcp.server")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_application_supports_protocol_discovery(app):
    async with Client(app.mcp) as client:
        listed = await client.list_tools()
    assert isinstance(listed.tools, list)
    # Every registered tool must be one of the five from spec.md section 3, and
    # no tool is registered twice. Subset check: registered tools can be fewer
    # than the full set while tools are still being implemented.
    names = [tool.name for tool in listed.tools]
    assert len(names) == len(set(names)), names
    assert set(names).issubset(EXPECTED_TOOLS), names


def test_default_startup_uses_stdio(app, monkeypatch, capsys):
    run = Mock()
    monkeypatch.setattr(app.mcp, "run", run)
    app.main([])
    run.assert_called_once_with(transport="stdio")
    assert capsys.readouterr().out == ""


def test_http_startup_uses_loopback(app, monkeypatch):
    run = Mock()
    monkeypatch.setattr(app.mcp, "run", run)
    app.main(["--http"])
    run.assert_called_once_with(transport="streamable-http", host="127.0.0.1", port=8000)


@pytest.fixture
def production_log_levels():
    """Root at INFO, as main() configures it, and no override on the httpx logger."""
    root, httpx_logger = logging.getLogger(), logging.getLogger("httpx")
    before = root.level, httpx_logger.level
    root.setLevel(logging.INFO)
    httpx_logger.setLevel(logging.NOTSET)
    yield
    root.setLevel(before[0])
    httpx_logger.setLevel(before[1])


def test_startup_keeps_request_urls_out_of_the_logs(app, monkeypatch, caplog, production_log_levels):
    """httpx logs every request URL, query string included, at INFO. A URL can carry a
    credential, so startup must stop that line from being written."""
    monkeypatch.setattr(app.mcp, "run", Mock())
    app.main([])
    transport = httpx.MockTransport(lambda request: httpx.Response(200))
    with httpx.Client(transport=transport) as client:
        client.get("https://example.test/search", params={"token": "synthetic-do-not-log"})
    assert "synthetic-do-not-log" not in caplog.text


def test_root_launcher_delegates_to_package(app, monkeypatch):
    main = Mock()
    monkeypatch.setattr(app, "main", main)
    runpy.run_path(str(ROOT / "server.py"), run_name="__main__")
    main.assert_called_once_with()


def test_unknown_arguments_do_not_start_server(app, monkeypatch):
    run = Mock()
    monkeypatch.setattr(app.mcp, "run", run)
    with pytest.raises(SystemExit) as error:
        app.main(["--unknown"])
    assert error.value.code == 2
    run.assert_not_called()

"""Offline checks for the shared application and its startup boundary."""
import importlib
import runpy
from pathlib import Path
from unittest.mock import Mock

import pytest
from mcp import Client

ROOT = Path(__file__).resolve().parents[1]


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
    # Tool-specific suites assert registrations; this check remains valid as tools land.
    names = [tool.name for tool in listed.tools]
    assert len(names) == len(set(names))


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

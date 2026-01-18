from pathlib import Path

from mcp_neo4j_cypher import server
from .helpers import build_async_driver


class DummyMCP:
    def __init__(self, name: str, **kwargs):
        self.name = name
        self.kwargs = kwargs

    def resource(self, *args, **kwargs):
        def decorator(fn):
            return fn

        return decorator

    def prompt(self, *args, **kwargs):
        def decorator(fn):
            return fn

        return decorator

    def tool(self, *args, **kwargs):
        def decorator(fn):
            return fn

        return decorator


def test_fastmcp_constructor_uses_supported_signature(monkeypatch):
    captured = {}

    def _capture_init(name: str, **kwargs):
        captured["name"] = name
        captured["kwargs"] = kwargs
        return DummyMCP(name, **kwargs)

    monkeypatch.setattr(server, "FastMCP", _capture_init)
    mcp = server.create_mcp_server(build_async_driver(), "neo4j")

    assert mcp is not None
    assert captured["name"] == "mcp-neo4j-cypher"
    assert "dependencies" not in captured["kwargs"]
    assert captured["kwargs"].get("stateless_http") is True


def test_format_startup_error_fastmcp_typeerror():
    message = server._format_startup_error(
        TypeError("FastMCP.__init__() got an unexpected keyword argument dependencies")
    )

    assert "FastMCP initialization rejected" in message


def test_contract_contains_startup_and_tool_schemas():
    contract_path = Path(
        "/Users/sylvain/Development/CopilotEA/mcp/mcp-neo4j/specs/"
        "001-fix-fastmcp-deps/contracts/server-startup.yaml"
    )
    contract_text = contract_path.read_text()

    assert "/status" in contract_text
    assert "/tools" in contract_text
    assert "StartupError" in contract_text
    assert "ToolDescriptor" in contract_text

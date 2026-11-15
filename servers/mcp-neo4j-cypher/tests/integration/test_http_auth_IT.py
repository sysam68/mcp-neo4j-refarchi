"""Authenticated RefArchi behavior through the real Streamable HTTP app."""

from unittest.mock import AsyncMock

from neo4j import AsyncDriver
from starlette.middleware import Middleware
from starlette.testclient import TestClient

from mcp_neo4j_cypher.auth import RefArchiAuthorizationMiddleware, build_auth_provider
from mcp_neo4j_cypher.server import create_mcp_server
from ..unit.test_auth import MCP_PATH, SETTINGS, mint, rpc, verifier


def test_authenticated_streamable_http_matrix(setup) -> None:
    driver = AsyncMock(spec=AsyncDriver)
    driver.execute_query.return_value = [{"label": "Architecture"}]
    auth_provider = build_auth_provider(SETTINGS, MCP_PATH, verifier())
    mcp = create_mcp_server(driver, "neo4j", auth_provider=auth_provider)
    app = mcp.http_app(
        path=MCP_PATH,
        middleware=[
            Middleware(
                RefArchiAuthorizationMiddleware,
                settings=SETTINGS,
                mcp_path=MCP_PATH,
            )
        ],
        json_response=True,
        stateless_http=True,
    )

    with TestClient(app, base_url="https://mcp.example.test") as client:
        anonymous = rpc(client, "tools/list", {})
        assert anonymous.status_code == 401
        assert SETTINGS.metadata_url in anonymous.headers["www-authenticate"]

        listed = rpc(client, "tools/list", {}, mint())
        assert listed.status_code == 200
        assert "get_all_labels" in {
            tool["name"] for tool in listed.json()["result"]["tools"]
        }

        bounded_read = rpc(
            client,
            "tools/call",
            {"name": "get_all_labels", "arguments": {}},
            mint(),
        )
        assert bounded_read.status_code == 200
        assert bounded_read.json()["result"]["isError"] is False
        driver.execute_query.assert_awaited_once()

        driver.reset_mock()
        denied_write = rpc(
            client,
            "tools/call",
            {
                "name": "write_neo4j_cypher",
                "arguments": {"query": "CREATE (:Denied)", "params": {}},
            },
            mint(),
        )
        assert denied_write.status_code == 403
        driver.execute_query.assert_not_awaited()

        denied_group = rpc(client, "tools/list", {}, mint(groups=("guests",)))
        assert denied_group.status_code == 403
        driver.execute_query.assert_not_awaited()

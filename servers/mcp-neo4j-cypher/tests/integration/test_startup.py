import pytest

from mcp_neo4j_cypher import server


@pytest.mark.asyncio(loop_scope="function")
async def test_startup_tools_available(mcp_server):
    read_tool = await mcp_server.get_tool("read_neo4j_cypher")
    write_tool = await mcp_server.get_tool("write_neo4j_cypher")

    assert read_tool is not None
    assert write_tool is not None


@pytest.mark.asyncio(loop_scope="function")
async def test_startup_read_only_excludes_write_tool(async_neo4j_driver):
    mcp = server.create_mcp_server(
        async_neo4j_driver,
        "neo4j",
        read_timeout=5,
        token_limit=128,
        read_only=True,
        config_sample_size=25,
    )

    read_tool = await mcp.get_tool("read_neo4j_cypher")
    assert read_tool is not None

    write_tool = await mcp.get_tool("write_neo4j_cypher")
    assert getattr(write_tool, "enabled", False) is False


@pytest.mark.asyncio(loop_scope="function")
async def test_startup_error_message_is_actionable(monkeypatch):
    class DummyDriver:
        async def close(self):
            return None

    monkeypatch.setattr(
        server.AsyncGraphDatabase, "driver", lambda *args, **kwargs: DummyDriver()
    )
    monkeypatch.setattr(
        server,
        "create_mcp_server",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            TypeError(
                "FastMCP.__init__() got an unexpected keyword argument dependencies"
            )
        ),
    )

    with pytest.raises(RuntimeError) as exc_info:
        await server.main(
            db_url="bolt://localhost:7687",
            username="neo4j",
            password="supersecret",
            database="neo4j",
            transport="stdio",
        )

    message = str(exc_info.value)
    assert "FastMCP initialization rejected" in message
    assert "supersecret" not in message

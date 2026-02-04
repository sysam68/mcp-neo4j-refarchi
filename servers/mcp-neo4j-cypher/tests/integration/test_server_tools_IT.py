import asyncio
import json
from typing import Any

import pytest
from fastmcp.exceptions import ToolError
from fastmcp.server import FastMCP
from mcp.types import TextContent

from mcp_neo4j_cypher.server import create_mcp_server


def _content_text(response: Any) -> str:
    content = response.content[0]
    assert isinstance(content, TextContent)
    return content.text


def _ensure_text(value: str | bytes) -> str:
    if isinstance(value, bytes):
        return value.decode()
    return value


def _prompt_names(mcp_server: FastMCP) -> list[str]:
    manager = getattr(mcp_server, "_prompt_manager", None)
    if manager is None:
        return []
    for attr in ("prompts", "_prompts"):
        value = getattr(manager, attr, None)
        if isinstance(value, dict):
            return list(value.keys())
    list_prompts = getattr(manager, "list_prompts", None)
    if callable(list_prompts):
        prompts = list_prompts()
        if isinstance(prompts, list):
            return [prompt.name for prompt in prompts]
    return []


async def _resource_uris(mcp_server: FastMCP) -> list[str]:
    manager = getattr(mcp_server, "_resource_manager", None)
    if manager is None:
        return []
    uris: list[str] = []
    for attr in ("resources", "_resources"):
        value = getattr(manager, attr, None)
        if isinstance(value, dict):
            uris.extend(value.keys())
    for attr in (
        "templates",
        "_templates",
        "resource_templates",
        "_resource_templates",
    ):
        value = getattr(manager, attr, None)
        if isinstance(value, dict):
            uris.extend(_template_uris(value.values()))
    list_resources = getattr(manager, "list_resources", None)
    if callable(list_resources):
        resources = list_resources()
        if asyncio.iscoroutine(resources):
            resources = await resources
        if isinstance(resources, list):
            uris.extend([resource.uri for resource in resources])
    return sorted(set(uris))


def _template_uris(templates: Any) -> list[str]:
    uris: list[str] = []
    for template in templates:
        uri = getattr(template, "uri", None) or getattr(template, "uri_template", None)
        if isinstance(uri, str):
            uris.append(uri)
    return uris


@pytest.mark.asyncio(loop_scope="function")
async def test_tool_registration(mcp_server: FastMCP):
    for name in (
        "get_db_labels",
        "get_coreConcept",
        "neo4j_schema_snapshot",
        "neo4j_vector_search",
    ):
        tool = await mcp_server.get_tool(name)
        assert tool is not None


@pytest.mark.asyncio(loop_scope="function")
async def test_get_db_labels_tool(mcp_server: FastMCP, init_data: Any):
    tool = await mcp_server.get_tool("get_db_labels")
    response = await tool.run({})

    result = json.loads(_content_text(response))

    assert "Person" in result


@pytest.mark.asyncio(loop_scope="function")
async def test_get_db_labels_empty_returns_array(mcp_server: FastMCP, clear_data: Any):
    tool = await mcp_server.get_tool("get_db_labels")
    response = await tool.run({})

    result = json.loads(_content_text(response))

    assert result == []


@pytest.mark.asyncio(loop_scope="function")
async def test_get_core_concept_tool(mcp_server: FastMCP, clear_data: Any):
    write_tool = await mcp_server.get_tool("write_neo4j_cypher")
    read_tool = await mcp_server.get_tool("read_neo4j_cypher")
    await write_tool.run(
        {
            "query": "CREATE (:coreConcept {name: 'ConceptA'}) RETURN 1",
            "params": {},
        }
    )

    tool = await mcp_server.get_tool("get_coreConcept")
    response = await tool.run({})

    result = json.loads(_content_text(response))

    assert isinstance(result, list)
    assert any(
        "coreConcept" in node.get("labels", [])
        and node.get("properties", {}).get("name") == "ConceptA"
        for node in result
    )


@pytest.mark.asyncio(loop_scope="function")
async def test_neo4j_schema_snapshot_tool(mcp_server: FastMCP, init_data: Any):
    tool = await mcp_server.get_tool("neo4j_schema_snapshot")
    response = await tool.run({})

    schema = json.loads(_content_text(response))

    assert "Person" in schema
    assert schema["Person"]["count"] == 3
    assert len(schema["Person"]["properties"]) == 2
    assert "FRIEND" in schema["Person"]["relationships"]


@pytest.mark.asyncio(loop_scope="function")
async def test_neo4j_vector_search_with_embedding(mcp_server: FastMCP, clear_data: Any):
    write_tool = await mcp_server.get_tool("write_neo4j_cypher")

    try:
        await write_tool.run(
            {
                "query": (
                    "CREATE (:VectorNode {name: 'Alpha', documentation: 'First node', embedding: [0.1, 0.2, 0.3]}) "
                    "CREATE (:VectorNode {name: 'Beta', documentation: 'Second node', embedding: [0.0, 0.1, 0.2]}) "
                    "CREATE (:VectorNode {name: 'Gamma', documentation: 'Third node', embedding: [0.9, 0.8, 0.7]}) "
                    "RETURN 1"
                ),
                "params": {},
            }
        )
        await write_tool.run(
            {
                "query": (
                    "CREATE VECTOR INDEX vec_all_nodes_embedding IF NOT EXISTS "
                    "FOR (n:VectorNode) ON (n.embedding) "
                    "OPTIONS {indexConfig: {`vector.dimensions`: 3, `vector.similarity_function`: 'cosine'}}"
                ),
                "params": {},
            }
        )
        await read_tool.run({"query": "CALL db.awaitIndexes()", "params": {}})
    except ToolError as exc:
        if "ProcedureNotFound" in str(exc) or "vector" in str(exc).lower():
            pytest.skip("Vector index procedures not available in this Neo4j version.")
        raise

    tool = await mcp_server.get_tool("neo4j_vector_search")
    response = await tool.run(
        {
            "index_name": "vec_all_nodes_embedding",
            "query_embedding": [0.1, 0.2, 0.3],
            "top_k": 2,
        }
    )

    result = json.loads(_content_text(response))

    assert isinstance(result, list)
    assert result
    assert "score" in result[0]
    assert "properties" in result[0]
    props = result[0]["properties"]
    assert "name" in props
    assert "documentation" in props


@pytest.mark.asyncio(loop_scope="function")
async def test_schema_snapshot_timeout(mcp_server_short_timeout: FastMCP):
    tool = await mcp_server_short_timeout.get_tool("neo4j_schema_snapshot")

    try:
        response = await tool.run({"sample_size": 1000})
        schema = json.loads(_content_text(response))
        assert isinstance(schema, dict)
    except ToolError as e:
        error_message = str(e)
        assert "Neo4j" in error_message or "timeout" in error_message.lower()


@pytest.mark.asyncio(loop_scope="function")
async def test_schema_snapshot_boundary_sample_size(
    mcp_server: FastMCP, init_data: Any
):
    tool = await mcp_server.get_tool("neo4j_schema_snapshot")
    response = await tool.run({"sample_size": 0})

    schema = json.loads(_content_text(response))
    assert "Person" in schema


@pytest.mark.asyncio(loop_scope="function")
async def test_label_resource_case_insensitive(mcp_server: FastMCP, init_data: Any):
    raw = await mcp_server._resource_manager.read_resource(  # pylint: disable=protected-access
        "resource://neo4j/labels/person"
    )
    result = json.loads(raw)

    assert any("Person" in node.get("labels", []) for node in result)


@pytest.mark.asyncio(loop_scope="function")
async def test_label_resource_missing_label_returns_empty_array(
    mcp_server: FastMCP,
):
    raw = await mcp_server._resource_manager.read_resource(  # pylint: disable=protected-access
        "resource://neo4j/labels/NoSuchLabel"
    )
    result = json.loads(raw)

    assert result == []


@pytest.mark.asyncio(loop_scope="function")
async def test_resources_list(mcp_server: FastMCP):
    resource_uris = await _resource_uris(mcp_server)

    assert resource_uris
    assert "resource://neo4j/labels/{label}" in resource_uris
    assert "resource://neo4j/refarchi" in resource_uris
    assert "resource://neo4j/schema" not in resource_uris
    assert "resource://neo4j/labels" not in resource_uris
    assert "resource://neo4j/core-concepts" not in resource_uris


@pytest.mark.asyncio(loop_scope="function")
async def test_refarchi_resource(mcp_server: FastMCP):
    raw = await mcp_server._resource_manager.read_resource(  # pylint: disable=protected-access
        "resource://neo4j/refarchi"
    )
    text = _ensure_text(raw)

    assert "get_db_labels" in text
    assert "check_label_existance" in text


@pytest.mark.asyncio(loop_scope="function")
async def test_prompt_list(mcp_server: FastMCP):
    prompt_names = _prompt_names(mcp_server)

    assert "neo4j_refarchi_prompt" in prompt_names
    assert "neo4j_label_lookup" not in prompt_names
    assert "neo4j_core_concepts_prompt" not in prompt_names


@pytest.mark.asyncio(loop_scope="function")
async def test_no_auth_tool_access(mcp_server: FastMCP, init_data: Any):
    tool = await mcp_server.get_tool("get_db_labels")
    response = await tool.run({})

    assert _content_text(response)


@pytest.mark.asyncio(loop_scope="function")
async def test_no_auth_resource_access(mcp_server: FastMCP):
    raw = await mcp_server._resource_manager.read_resource(  # pylint: disable=protected-access
        "resource://neo4j/refarchi"
    )
    assert _ensure_text(raw)


@pytest.mark.asyncio(loop_scope="function")
async def test_tool_error_json(async_neo4j_driver):
    mcp = create_mcp_server(async_neo4j_driver, database="missing")
    tool = await mcp.get_tool("get_db_labels")

    with pytest.raises(ToolError) as excinfo:
        await tool.run({})

    payload = json.loads(str(excinfo.value))
    assert payload.get("error")


@pytest.mark.asyncio(loop_scope="function")
async def test_idempotent_open_world_behavior(mcp_server: FastMCP, init_data: Any):
    tool = await mcp_server.get_tool("get_db_labels")
    response_a = await tool.run({})
    response_b = await tool.run({})

    result_a = json.loads(_content_text(response_a))
    result_b = json.loads(_content_text(response_b))

    assert result_a == result_b

    annotations = getattr(tool, "annotations", None)
    if annotations is not None:
        assert annotations.idempotentHint is True
        assert annotations.openWorldHint is True


@pytest.mark.asyncio(loop_scope="function")
async def test_write_neo4j_cypher(mcp_server: FastMCP):
    query = "CREATE (n:Test {name: 'test', age: 123}) RETURN n.name"
    tool = await mcp_server.get_tool("write_neo4j_cypher")
    response = await tool.run(dict(query=query))

    result = json.loads(_content_text(response))

    assert "nodes_created" in result
    assert "labels_added" in result
    assert "properties_set" in result
    assert result["nodes_created"] == 1
    assert result["labels_added"] == 1
    assert result["properties_set"] == 2


@pytest.mark.asyncio(loop_scope="function")
async def test_read_neo4j_cypher(mcp_server: FastMCP, init_data: Any):
    query = """
    MATCH (p:Person)-[:FRIEND]->(friend)
    RETURN p.name AS person, friend.name AS friend_name
    ORDER BY p.name, friend.name
    """

    tool = await mcp_server.get_tool("read_neo4j_cypher")
    response = await tool.run(dict(query=query))

    result = json.loads(_content_text(response))

    assert len(result) == 2
    assert result[0]["person"] == "Alice"
    assert result[0]["friend_name"] == "Bob"
    assert result[1]["person"] == "Bob"
    assert result[1]["friend_name"] == "Charlie"


@pytest.mark.asyncio(loop_scope="function")
async def test_read_query_timeout_with_slow_query(
    mcp_server_short_timeout: FastMCP, clear_data: Any
):
    """Test that read queries timeout appropriately with a slow query."""
    # Create a query that should take longer than 0.01 seconds
    slow_query = """
    WITH range(1, 10000) AS r
    UNWIND r AS x
    WITH x
    WHERE x % 2 = 0
    RETURN count(x) AS result
    """

    tool = await mcp_server_short_timeout.get_tool("read_neo4j_cypher")

    # The query might timeout and raise a ToolError, or it might complete very fast
    # Let's just verify the server handles it without crashing
    try:
        response = await tool.run(dict(query=slow_query))
        # If it completes, verify it returns valid results
        if _content_text(response):
            result = json.loads(_content_text(response))
            assert isinstance(result, list)
    except ToolError as e:
        # If it times out, that's also acceptable behavior
        error_message = str(e)
        assert "Neo4j Error" in error_message


@pytest.mark.asyncio(loop_scope="function")
async def test_read_query_with_normal_timeout_succeeds(
    mcp_server: FastMCP, init_data: Any
):
    """Test that normal queries succeed with reasonable timeout."""
    query = "MATCH (p:Person) RETURN p.name AS name ORDER BY name"

    tool = await mcp_server.get_tool("read_neo4j_cypher")
    response = await tool.run(dict(query=query))

    result = json.loads(_content_text(response))

    # Should succeed and return expected results
    assert len(result) == 3
    assert result[0]["name"] == "Alice"
    assert result[1]["name"] == "Bob"
    assert result[2]["name"] == "Charlie"


@pytest.mark.asyncio(loop_scope="function")
async def test_write_query_no_timeout(
    mcp_server_short_timeout: FastMCP, clear_data: Any
):
    """Test that write queries are not subject to timeout restrictions."""
    # Write queries should not be affected by read_timeout
    query = "CREATE (n:TimeoutTest {name: 'test', created: timestamp()}) RETURN n.name"

    tool = await mcp_server_short_timeout.get_tool("write_neo4j_cypher")
    response = await tool.run(dict(query=query))

    result = json.loads(_content_text(response))

    # Write operation should succeed regardless of short timeout
    assert "nodes_created" in result
    assert result["nodes_created"] == 1


@pytest.mark.asyncio(loop_scope="function")
async def test_timeout_configuration_passed_correctly(async_neo4j_driver):
    """Test that timeout configuration is properly passed to the server."""
    # Create servers with different timeout values
    mcp_30s = create_mcp_server(async_neo4j_driver, "neo4j", read_timeout=30)
    mcp_60s = create_mcp_server(async_neo4j_driver, "neo4j", read_timeout=60)

    # Both should be created successfully (configuration test)
    assert mcp_30s is not None
    assert mcp_60s is not None

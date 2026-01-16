import json
import logging
import re
from typing import Any, Literal, Optional

from fastmcp.exceptions import ResourceError, ToolError
from fastmcp.server import FastMCP
from fastmcp.tools.tool import TextContent, ToolResult
from mcp.types import ToolAnnotations
from neo4j import AsyncDriver, AsyncGraphDatabase, Query, RoutingControl
from neo4j.exceptions import ClientError, Neo4jError
from pydantic import Field
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .utils import _truncate_string_to_tokens, _value_sanitize

logger = logging.getLogger("mcp_neo4j_cypher")


def _format_namespace(namespace: str) -> str:
    if namespace:
        if namespace.endswith("-"):
            return namespace
        else:
            return namespace + "-"
    else:
        return ""


def _is_write_query(query: str) -> bool:
    """Check if the query is a write query."""
    return (
        re.search(r"\b(MERGE|CREATE|INSERT|SET|DELETE|REMOVE|ADD)\b", query, re.IGNORECASE)
        is not None
    )


def create_mcp_server(
    neo4j_driver: AsyncDriver,
    database: str = "neo4j",
    namespace: str = "",
    read_timeout: int = 30,
    token_limit: Optional[int] = None,
    read_only: bool = False,
    config_sample_size: int = 1000,
) -> FastMCP:
    mcp: FastMCP = FastMCP(
        "mcp-neo4j-cypher", dependencies=["neo4j", "pydantic"], stateless_http=True
    )

    namespace_prefix = _format_namespace(namespace)
    allow_writes = not read_only

    async def _execute_read_query(
        query: str, params: Optional[dict[str, Any]] = None
    ) -> list[dict[str, Any]]:
        query_obj = Query(query, timeout=float(read_timeout))
        return await neo4j_driver.execute_query(
            query_obj,
            parameters_=params or {},
            routing_control=RoutingControl.READ,
            database_=database,
            result_transformer_=lambda r: r.data(),
        )

    def _clean_schema(schema: dict) -> dict:
        cleaned = {}

        for key, entry in schema.items():
            new_entry = {"type": entry["type"]}
            if "count" in entry:
                new_entry["count"] = entry["count"]

            labels = entry.get("labels", [])
            if labels:
                new_entry["labels"] = labels

            props = entry.get("properties", {})
            clean_props = {}
            for pname, pinfo in props.items():
                cp = {}
                if "indexed" in pinfo:
                    cp["indexed"] = pinfo["indexed"]
                if "type" in pinfo:
                    cp["type"] = pinfo["type"]
                if cp:
                    clean_props[pname] = cp
            if clean_props:
                new_entry["properties"] = clean_props

            if entry.get("relationships"):
                rels_out = {}
                for rel_name, rel in entry["relationships"].items():
                    cr = {}
                    if "direction" in rel:
                        cr["direction"] = rel["direction"]
                    # nested labels
                    rlabels = rel.get("labels", [])
                    if rlabels:
                        cr["labels"] = rlabels
                    # nested properties
                    rprops = rel.get("properties", {})
                    clean_rprops = {}
                    for rpname, rpinfo in rprops.items():
                        crp = {}
                        if "indexed" in rpinfo:
                            crp["indexed"] = rpinfo["indexed"]
                        if "type" in rpinfo:
                            crp["type"] = rpinfo["type"]
                        if crp:
                            clean_rprops[rpname] = crp
                    if clean_rprops:
                        cr["properties"] = clean_rprops

                    if cr:
                        rels_out[rel_name] = cr

                if rels_out:
                    new_entry["relationships"] = rels_out

            cleaned[key] = new_entry

        return cleaned

    @mcp.resource(
        "resource://neo4j/schema{?sample_size}",
        name="neo4j_schema",
        title="Neo4j Schema",
        description="APOC-derived schema snapshot of the Neo4j database.",
        mime_type="application/json",
    )
    async def neo4j_schema(
        sample_size: int = Field(
            default=config_sample_size,
            description=(
                "Sample size used for APOC schema inference. Use -1 for full scan."
            ),
        ),
    ) -> dict[str, Any]:
        """
        Returns nodes, their properties (with types and indexed flags), and relationships
        using APOC's schema inspection.

        Performance Notes:
            - If `sample_size` is not provided, uses the server's default sample setting.
            - If retrieving the schema times out, try lowering the sample size.
            - To sample the entire graph use `sample_size=-1`.
        """

        effective_sample_size = sample_size if sample_size else config_sample_size
        logger.info(
            "Reading Neo4j schema resource with sample size "
            f"{effective_sample_size}."
        )

        get_schema_query = (
            "CALL apoc.meta.schema({sample: "
            f"{effective_sample_size}"
            "}) YIELD value RETURN value"
        )

        try:
            results_json = await _execute_read_query(get_schema_query)
            logger.debug(f"Schema query returned {len(results_json)} rows")

            schema_clean = _clean_schema(results_json[0].get("value"))

            return schema_clean

        except ClientError as e:
            if "Neo.ClientError.Procedure.ProcedureNotFound" in str(e):
                raise ResourceError(
                    "Neo4j Client Error: This instance of Neo4j does not have the APOC "
                    "plugin installed. Please install and enable the APOC plugin to "
                    "use the `resource://neo4j/schema` resource."
                )
            raise ResourceError(f"Neo4j Client Error: {e}")

        except Neo4jError as e:
            raise ResourceError(f"Neo4j Error: {e}")

        except Exception as e:
            logger.error(f"Error retrieving Neo4j schema resource: {e}")
            raise ResourceError(f"Unexpected Error: {e}")

    @mcp.resource(
        "resource://neo4j/labels",
        name="neo4j_labels",
        title="Neo4j Labels",
        description="List all labels present in the database.",
        mime_type="application/json",
    )
    async def neo4j_labels() -> list[str]:
        """List all labels in the Neo4j database."""

        get_labels_query = "CALL db.labels()"

        try:
            results = await _execute_read_query(get_labels_query)
            labels = [row.get("label") for row in results if "label" in row]
            logger.debug(f"Label resource returned {len(labels)} labels")
            return labels

        except Neo4jError as e:
            logger.error(
                f"Neo4j Error executing label resource query: {e}\n{get_labels_query}"
            )
            raise ResourceError(f"Neo4j Error: {e}\n{get_labels_query}")

        except Exception as e:
            logger.error(
                f"Error executing label resource query: {e}\n{get_labels_query}"
            )
            raise ResourceError(f"Error: {e}\n{get_labels_query}")

    @mcp.resource(
        "resource://neo4j/labels/{label}",
        name="neo4j_label_nodes",
        title="Neo4j Label Nodes",
        description="Nodes matching a label name (case-insensitive).",
        mime_type="application/json",
    )
    async def neo4j_label_nodes(
        label: str = Field(
            ...,
            description="Label name to match (case-insensitive).",
        ),
    ) -> list[dict[str, Any]]:
        """Find nodes that match a label name, case-insensitively."""

        get_labels_by_name_query = """
WITH toLower($targetLabel) AS targetLabelLower
MATCH (n)
WHERE any(label IN labels(n)
          WHERE toLower(label) = targetLabelLower)
RETURN n
"""

        try:
            results = await _execute_read_query(
                get_labels_by_name_query, {"targetLabel": label}
            )
            sanitized_results = [_value_sanitize(el) for el in results]
            logger.debug(
                "Label resource query returned "
                f"{len(sanitized_results)} rows"
            )
            return sanitized_results

        except Neo4jError as e:
            logger.error(
                "Neo4j Error executing label resource query: "
                f"{e}\n{get_labels_by_name_query}\n{label}"
            )
            raise ResourceError(
                f"Neo4j Error: {e}\n{get_labels_by_name_query}\n{label}"
            )

        except Exception as e:
            logger.error(
                "Error executing label resource query: "
                f"{e}\n{get_labels_by_name_query}\n{label}"
            )
            raise ResourceError(
                f"Error: {e}\n{get_labels_by_name_query}\n{label}"
            )

    @mcp.resource(
        "resource://neo4j/core-concepts",
        name="neo4j_core_concepts",
        title="Neo4j Core Concepts",
        description="List nodes labeled coreConcept.",
        mime_type="application/json",
    )
    async def neo4j_core_concepts() -> list[dict[str, Any]]:
        """List coreConcept nodes from the Neo4j database."""

        get_core_concept_query = "MATCH (n:coreConcept) RETURN n"

        try:
            results = await _execute_read_query(get_core_concept_query)
            sanitized_results = [_value_sanitize(el) for el in results]
            logger.debug(
                "Core concept resource returned "
                f"{len(sanitized_results)} rows"
            )
            return sanitized_results

        except Neo4jError as e:
            logger.error(
                "Neo4j Error executing core concept resource query: "
                f"{e}\n{get_core_concept_query}"
            )
            raise ResourceError(f"Neo4j Error: {e}\n{get_core_concept_query}")

        except Exception as e:
            logger.error(
                "Error executing core concept resource query: "
                f"{e}\n{get_core_concept_query}"
            )
            raise ResourceError(f"Error: {e}\n{get_core_concept_query}")

    @mcp.prompt(title="Neo4j Schema Snapshot")
    def neo4j_schema_snapshot(
        sample_size: int = Field(
            default=config_sample_size,
            description=(
                "Sample size used for APOC schema inference. Use -1 for full scan."
            ),
        ),
    ) -> str:
        """Prompt helper to summarize the Neo4j schema resource."""

        return (
            "Use the Neo4j schema resource to understand labels, properties, and "
            "relationships. Read:\n"
            f"resource://neo4j/schema?sample_size={sample_size}\n\n"
            "Then summarize the key node labels, relationship types, and indexed "
            "properties."
        )

    @mcp.prompt(title="Neo4j Label Lookup")
    def neo4j_label_lookup(
        label: str = Field(
            ...,
            description="Label name to match (case-insensitive).",
        ),
    ) -> str:
        """Prompt helper to retrieve nodes by label."""

        return (
            "Read nodes for the requested label using the resource template:\n"
            f"resource://neo4j/labels/{label}\n\n"
            "Then summarize the key properties found on those nodes."
        )

    @mcp.prompt(title="Neo4j Core Concepts")
    def neo4j_core_concepts_prompt() -> str:
        """Prompt helper to explore coreConcept nodes."""

        return (
            "Read core concepts using the resource:\n"
            "resource://neo4j/core-concepts\n\n"
            "Then summarize the coreConcept nodes and their key properties."
        )

    @mcp.tool(
        name=namespace_prefix + "read_neo4j_cypher",
        annotations=ToolAnnotations(
            title="Read Neo4j Cypher",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def read_neo4j_cypher(
        query: str = Field(..., description="The Cypher query to execute."),
        params: dict[str, Any] = Field(
            dict(), description="The parameters to pass to the Cypher query."
        ),
    ) -> list[ToolResult]:
        """Execute a read Cypher query on the neo4j database."""

        if _is_write_query(query):
            raise ValueError("Only MATCH queries are allowed for read-query")

        try:
            query_obj = Query(query, timeout=float(read_timeout))
            results = await neo4j_driver.execute_query(
                query_obj,
                parameters_=params,
                routing_control=RoutingControl.READ,
                database_=database,
                result_transformer_=lambda r: r.data(),
            )
            sanitized_results = [_value_sanitize(el) for el in results]
            results_json_str = json.dumps(sanitized_results, default=str)
            if token_limit:
                results_json_str = _truncate_string_to_tokens(
                    results_json_str, token_limit
                )

            logger.debug(f"Read query returned {len(results_json_str)} rows")

            return ToolResult(content=[TextContent(type="text", text=results_json_str)])

        except Neo4jError as e:
            logger.error(f"Neo4j Error executing read query: {e}\n{query}\n{params}")
            raise ToolError(f"Neo4j Error: {e}\n{query}\n{params}")

        except Exception as e:
            logger.error(f"Error executing read query: {e}\n{query}\n{params}")
            raise ToolError(f"Error: {e}\n{query}\n{params}")

    @mcp.tool(
        name=namespace_prefix + "write_neo4j_cypher",
        annotations=ToolAnnotations(
            title="Write Neo4j Cypher",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=True,
        ),
        enabled=allow_writes,
    )
    async def write_neo4j_cypher(
        query: str = Field(..., description="The Cypher query to execute."),
        params: dict[str, Any] = Field(
            dict(), description="The parameters to pass to the Cypher query."
        ),
    ) -> list[ToolResult]:
        """Execute a write Cypher query on the neo4j database."""

        if not _is_write_query(query):
            raise ValueError("Only write queries are allowed for write-query")

        try:
            _, summary, _ = await neo4j_driver.execute_query(
                query,
                parameters_=params,
                routing_control=RoutingControl.WRITE,
                database_=database,
            )

            counters_json_str = json.dumps(summary.counters.__dict__, default=str)

            logger.debug(f"Write query affected {counters_json_str}")

            return ToolResult(
                content=[TextContent(type="text", text=counters_json_str)]
            )

        except Neo4jError as e:
            logger.error(f"Neo4j Error executing write query: {e}\n{query}\n{params}")
            raise ToolError(f"Neo4j Error: {e}\n{query}\n{params}")

        except Exception as e:
            logger.error(f"Error executing write query: {e}\n{query}\n{params}")
            raise ToolError(f"Error: {e}\n{query}\n{params}")

    return mcp


async def main(
    db_url: str,
    username: str,
    password: str,
    database: str,
    transport: Literal["stdio", "sse", "http"] = "stdio",
    namespace: str = "",
    host: str = "127.0.0.1",
    port: int = 8000,
    path: str = "/mcp/",
    allow_origins: list[str] = [],
    allowed_hosts: list[str] = [],
    read_timeout: int = 30,
    token_limit: Optional[int] = None,
    read_only: bool = False,
    schema_sample_size: Optional[int] = None, # this is known as the config_sample_size in the create_mcp_server function
) -> None:
    logger.info("Starting MCP neo4j Server")

    neo4j_driver = AsyncGraphDatabase.driver(
        db_url,
        auth=(
            username,
            password,
        ),
    )
    custom_middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=allow_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["*"],
        ),
        Middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts),
    ]

    mcp = create_mcp_server(
        neo4j_driver, database, namespace, read_timeout, token_limit, read_only, schema_sample_size
    )

    # Run the server with the specified transport
    match transport:
        case "http":
            logger.info(
                f"Running Neo4j Cypher MCP Server with HTTP transport on {host}:{port}..."
            )
            await mcp.run_http_async(
                host=host, port=port, path=path, middleware=custom_middleware
            )
        case "stdio":
            logger.info("Running Neo4j Cypher MCP Server with stdio transport...")
            await mcp.run_stdio_async()
        case "sse":
            logger.info(
                f"Running Neo4j Cypher MCP Server with SSE transport on {host}:{port}..."
            )
            await mcp.run_http_async(
                host=host,
                port=port,
                path=path,
                middleware=custom_middleware,
                transport="sse",
            )
        case _:
            logger.error(
                f"Invalid transport: {transport} | Must be either 'stdio', 'sse', or 'http'"
            )
            raise ValueError(
                f"Invalid transport: {transport} | Must be either 'stdio', 'sse', or 'http'"
            )


if __name__ == "__main__":
    main()

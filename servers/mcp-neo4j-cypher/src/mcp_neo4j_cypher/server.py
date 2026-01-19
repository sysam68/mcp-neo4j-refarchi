import builtins
import json
import logging
import re
from http import HTTPStatus
from typing import Any, Literal, LiteralString, Optional, cast

import anyio
from fastmcp.exceptions import ResourceError, ToolError
from fastmcp.server import FastMCP
from fastmcp.tools.tool import ToolResult  # type: ignore[reportPrivateImportUsage]
from mcp.shared.message import ServerMessageMetadata, SessionMessage
from mcp.types import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    PARSE_ERROR,
    JSONRPCError,
    JSONRPCMessage,
    JSONRPCRequest,
    JSONRPCResponse,
    TextContent,
    ToolAnnotations,
)
from neo4j import AsyncDriver, AsyncGraphDatabase, Query, RoutingControl
from neo4j.exceptions import ClientError, Neo4jError
from pydantic import Field, ValidationError
from sse_starlette import EventSourceResponse
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import ClientDisconnect, Request

from .utils import _truncate_string_to_tokens, _value_sanitize

logger = logging.getLogger("mcp_neo4j_cypher")
TOOL_NAMES = {
    "read": "read_neo4j_cypher",
    "write": "write_neo4j_cypher",
}
_BASE_EXCEPTION_GROUP = getattr(builtins, "BaseExceptionGroup", None)


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
        re.search(
            r"\b(MERGE|CREATE|INSERT|SET|DELETE|REMOVE|ADD)\b", query, re.IGNORECASE
        )
        is not None
    )


def _format_startup_error(exc: Exception) -> str:
    if isinstance(exc, TypeError) and "FastMCP" in str(exc):
        return (
            "Startup failed: FastMCP initialization rejected an unsupported argument. "
            "Ensure the server uses the supported FastMCP constructor for the installed version."
        )
    return (
        "Startup failed during server initialization. "
        f"Reason: {exc.__class__.__name__}."
    )


def _is_disconnect_exception(exc: BaseException) -> bool:
    if isinstance(
        exc,
        (
            ClientDisconnect,
            anyio.ClosedResourceError,
            anyio.BrokenResourceError,
        ),
    ):
        return True
    if _BASE_EXCEPTION_GROUP and isinstance(exc, _BASE_EXCEPTION_GROUP):
        exceptions = getattr(exc, "exceptions", ())
        return any(_is_disconnect_exception(child) for child in exceptions)
    return False


def _log_disconnect_event(
    exc: BaseException,
    request: Optional[Request] = None,
) -> None:
    details = ""
    if request is not None:
        details = f" method={request.method} path={request.url.path}"
    logger.info(
        "Client disconnect handled%s (%s).",
        details,
        exc.__class__.__name__,
    )


def _patch_streamable_http_disconnect_handling() -> None:
    import mcp.server.streamable_http as streamable_http

    streamable_http_state = cast(Any, streamable_http)

    if getattr(streamable_http_state, "_mcp_neo4j_disconnect_patch", False):
        return

    StreamableHTTPServerTransport = streamable_http.StreamableHTTPServerTransport

    async def _patched_handle_post_request(
        self,
        scope,
        request,
        receive,
        send,
    ) -> None:
        writer = self._read_stream_writer
        if writer is None:  # pragma: no cover
            raise ValueError(
                "No read stream writer available. Ensure connect() is called first."
            )

        async def _safe_send_response(response) -> bool:
            try:
                await response(scope, receive, send)
            except Exception as send_err:
                if _is_disconnect_exception(send_err):
                    _log_disconnect_event(send_err, request=request)
                    return False
                raise
            return True

        try:
            if hasattr(self, "_validate_accept_header"):
                if not await self._validate_accept_header(request, scope, send):
                    return
            else:
                has_json, has_sse = self._check_accept_headers(request)
                if self.is_json_response_enabled:
                    if not has_json:
                        response = self._create_error_response(
                            "Not Acceptable: Client must accept application/json",
                            HTTPStatus.NOT_ACCEPTABLE,
                        )
                        if not await _safe_send_response(response):
                            return
                        return
                elif not (has_json and has_sse):
                    response = self._create_error_response(
                        "Not Acceptable: Client must accept both application/json and text/event-stream",
                        HTTPStatus.NOT_ACCEPTABLE,
                    )
                    if not await _safe_send_response(response):
                        return
                    return

            if not self._check_content_type(request):  # pragma: no cover
                response = self._create_error_response(
                    "Unsupported Media Type: Content-Type must be application/json",
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                )
                if not await _safe_send_response(response):
                    return
                return

            try:
                body = await request.body()
            except ClientDisconnect as exc:
                _log_disconnect_event(exc, request=request)
                return

            try:
                raw_message = json.loads(body)
            except json.JSONDecodeError as exc:
                response = self._create_error_response(
                    f"Parse error: {str(exc)}",
                    HTTPStatus.BAD_REQUEST,
                    PARSE_ERROR,
                )
                if not await _safe_send_response(response):
                    return
                return

            try:  # pragma: no cover
                message = JSONRPCMessage.model_validate(raw_message)
            except ValidationError as exc:  # pragma: no cover
                response = self._create_error_response(
                    f"Validation error: {str(exc)}",
                    HTTPStatus.BAD_REQUEST,
                    INVALID_PARAMS,
                )
                if not await _safe_send_response(response):
                    return
                return

            is_initialization_request = (
                isinstance(message.root, JSONRPCRequest)
                and message.root.method == "initialize"
            )  # pragma: no cover

            if is_initialization_request:  # pragma: no cover
                if self.mcp_session_id:
                    request_session_id = self._get_session_id(request)
                    if request_session_id and request_session_id != self.mcp_session_id:
                        response = self._create_error_response(
                            "Not Found: Invalid or expired session ID",
                            HTTPStatus.NOT_FOUND,
                        )
                        if not await _safe_send_response(response):
                            return
                        return
            elif not await self._validate_request_headers(
                request, send
            ):  # pragma: no cover
                return

            if not isinstance(message.root, JSONRPCRequest):  # pragma: no cover
                response = self._create_json_response(
                    None,
                    HTTPStatus.ACCEPTED,
                )
                if not await _safe_send_response(response):
                    return

                metadata = ServerMessageMetadata(request_context=request)
                session_message = SessionMessage(message, metadata=metadata)
                await writer.send(session_message)
                return

            request_id = str(message.root.id)  # pragma: no cover
            self._request_streams[request_id] = anyio.create_memory_object_stream(
                0
            )  # pragma: no cover
            request_stream_reader = self._request_streams[request_id][
                1
            ]  # pragma: no cover

            if self.is_json_response_enabled:  # pragma: no cover
                metadata = ServerMessageMetadata(request_context=request)
                session_message = SessionMessage(message, metadata=metadata)
                await writer.send(session_message)
                try:
                    response_message = None

                    async for event_message in request_stream_reader:
                        if isinstance(
                            event_message.message.root,
                            JSONRPCResponse | JSONRPCError,
                        ):
                            response_message = event_message.message
                            break
                        logger.debug(
                            "received: %s",
                            event_message.message.root.method,
                        )

                    if response_message:
                        response = self._create_json_response(response_message)
                        if not await _safe_send_response(response):
                            return
                    else:
                        logger.error(
                            "No response message received before stream closed"
                        )
                        response = self._create_error_response(
                            "Error processing request: No response received",
                            HTTPStatus.INTERNAL_SERVER_ERROR,
                        )
                        if not await _safe_send_response(response):
                            return
                except Exception:
                    logger.exception("Error processing JSON response")
                    response = self._create_error_response(
                        "Error processing request",
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        INTERNAL_ERROR,
                    )
                    if not await _safe_send_response(response):
                        return
                finally:
                    await self._clean_up_memory_streams(request_id)
            else:  # pragma: no cover
                sse_stream_writer, sse_stream_reader = (
                    anyio.create_memory_object_stream(0)
                )

                if hasattr(self, "_sse_stream_writers"):
                    self._sse_stream_writers[request_id] = sse_stream_writer

                async def sse_writer():
                    try:
                        async with sse_stream_writer, request_stream_reader:
                            if hasattr(self, "_send_priming_event"):
                                await self._send_priming_event(
                                    request_id,
                                    sse_stream_writer,
                                )

                            async for event_message in request_stream_reader:
                                event_data = self._create_event_data(event_message)
                                await sse_stream_writer.send(event_data)
                                if isinstance(
                                    event_message.message.root,
                                    JSONRPCResponse | JSONRPCError,
                                ):
                                    break
                    except anyio.ClosedResourceError:
                        logger.debug("SSE stream closed by close_sse_stream()")
                    except Exception:
                        logger.exception("Error in SSE writer")
                    finally:
                        logger.debug("Closing SSE writer")
                        if hasattr(self, "_sse_stream_writers"):
                            self._sse_stream_writers.pop(request_id, None)
                        await self._clean_up_memory_streams(request_id)

                headers = {
                    "Cache-Control": "no-cache, no-transform",
                    "Connection": "keep-alive",
                    "Content-Type": streamable_http.CONTENT_TYPE_SSE,
                    **(
                        {streamable_http.MCP_SESSION_ID_HEADER: self.mcp_session_id}
                        if self.mcp_session_id
                        else {}
                    ),
                }
                response = EventSourceResponse(
                    content=sse_stream_reader,
                    data_sender_callable=sse_writer,
                    headers=headers,
                )

                try:
                    async with anyio.create_task_group() as tg:
                        tg.start_soon(response, scope, receive, send)
                        if hasattr(self, "_create_session_message"):
                            session_message = self._create_session_message(
                                message,
                                request,
                                request_id,
                            )
                        else:
                            metadata = ServerMessageMetadata(request_context=request)
                            session_message = SessionMessage(
                                message,
                                metadata=metadata,
                            )
                        await writer.send(session_message)
                except Exception:
                    logger.exception("SSE response error")
                    await sse_stream_writer.aclose()
                    await sse_stream_reader.aclose()
                    await self._clean_up_memory_streams(request_id)

        except Exception as err:  # pragma: no cover
            if _is_disconnect_exception(err):
                _log_disconnect_event(err, request=request)
                return

            logger.exception("Error handling POST request")
            response = self._create_error_response(
                f"Error handling POST request: {err}",
                HTTPStatus.INTERNAL_SERVER_ERROR,
                INTERNAL_ERROR,
            )
            try:
                await response(scope, receive, send)
            except Exception as send_err:
                if _is_disconnect_exception(send_err):
                    _log_disconnect_event(send_err, request=request)
                    return
                raise
            if writer:
                try:
                    await writer.send(Exception(err))
                except Exception as send_err:
                    if _is_disconnect_exception(send_err):
                        _log_disconnect_event(send_err, request=request)
                        return
                    raise
            return

    StreamableHTTPServerTransport._handle_post_request = _patched_handle_post_request
    streamable_http_state._mcp_neo4j_disconnect_patch = True


def _patch_mcp_send_log_message() -> None:
    import mcp.server.session as mcp_session

    mcp_session_state = cast(Any, mcp_session)

    if getattr(mcp_session_state, "_mcp_neo4j_log_patch", False):
        return

    original_send_log_message = mcp_session.ServerSession.send_log_message

    async def _wrapped_send_log_message(self, *args, **kwargs):
        try:
            return await original_send_log_message(self, *args, **kwargs)
        except Exception as exc:
            if _is_disconnect_exception(exc):
                _log_disconnect_event(exc)
                return None
            raise

    mcp_session.ServerSession.send_log_message = _wrapped_send_log_message
    mcp_session_state._mcp_neo4j_log_patch = True


def create_mcp_server(
    neo4j_driver: AsyncDriver,
    database: str = "neo4j",
    namespace: str = "",
    read_timeout: int = 30,
    token_limit: Optional[int] = None,
    read_only: bool = False,
    config_sample_size: int = 1000,
) -> FastMCP:
    _patch_streamable_http_disconnect_handling()
    _patch_mcp_send_log_message()

    mcp: FastMCP = FastMCP("mcp-neo4j-cypher", stateless_http=True)

    namespace_prefix = _format_namespace(namespace)
    allow_writes = not read_only

    async def _execute_read_query(
        query: str, params: Optional[dict[str, Any]] = None
    ) -> list[dict[str, Any]]:
        query_obj = Query(cast(LiteralString, query), timeout=float(read_timeout))
        return await neo4j_driver.execute_query(
            query_obj,
            parameters_=params or {},
            routing_control=RoutingControl.READ,
            database_=database,
            result_transformer_=lambda r: r.data(),
        )

    def _tool_error_json(error: str, **context: Any) -> ToolError:
        payload: dict[str, Any] = {"error": error}
        if context:
            payload["context"] = context
        return ToolError(json.dumps(payload, default=str))

    def _tool_result(payload: Any) -> ToolResult:
        return ToolResult(
            content=[
                TextContent(
                    type="text",
                    text=cast(LiteralString, json.dumps(payload, default=str)),
                )
            ]
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

    def _node_from_row(row: dict[str, Any]) -> dict[str, Any]:
        labels = row.get("labels") or []
        if isinstance(labels, (set, tuple)):
            labels = list(labels)
        properties = row.get("properties") or {}
        if not isinstance(properties, dict):
            properties = {}
        return {
            "id": row.get("id"),
            "labels": labels,
            "properties": _value_sanitize(properties),
        }

    async def _read_schema(sample_size: int) -> dict[str, Any]:
        effective_sample_size = sample_size if sample_size else config_sample_size
        logger.info(
            f"Reading Neo4j schema snapshot with sample size {effective_sample_size}."
        )

        get_schema_query = (
            "CALL apoc.meta.schema({sample: "
            f"{effective_sample_size}"
            "}) YIELD value RETURN value"
        )

        try:
            results_json = await _execute_read_query(get_schema_query)
            logger.debug(f"Schema query returned {len(results_json)} rows")

            raw_schema = results_json[0].get("value") or {}
            schema_clean = _clean_schema(cast(dict, raw_schema))

            return schema_clean

        except ClientError as e:
            if "Neo.ClientError.Procedure.ProcedureNotFound" in str(e):
                raise _tool_error_json(
                    "Neo4j Client Error",
                    details=(
                        "APOC is not installed. Install and enable APOC to use "
                        "neo4j_schema_snapshot."
                    ),
                )
            raise _tool_error_json("Neo4j Client Error", details=str(e))

        except Neo4jError as e:
            raise _tool_error_json("Neo4j Error", details=str(e))

        except Exception as e:
            logger.error(f"Error retrieving Neo4j schema snapshot: {e}")
            raise _tool_error_json("Unexpected Error", details=str(e))

    @mcp.tool(
        name=namespace_prefix + "get_db_labels",
        annotations=ToolAnnotations(
            title="Get Neo4j Labels",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def get_db_labels() -> ToolResult:
        """Return all labels in the Neo4j database."""

        get_labels_query = "CALL db.labels()"

        try:
            results = await _execute_read_query(get_labels_query)
            labels = [
                row["label"] for row in results if isinstance(row.get("label"), str)
            ]
            logger.debug(f"Label tool returned {len(labels)} labels")
            return _tool_result(labels)

        except Neo4jError as e:
            logger.error(f"Neo4j Error executing label tool: {e}\n{get_labels_query}")
            raise _tool_error_json(
                "Neo4j Error", details=str(e), query=get_labels_query
            )

        except Exception as e:
            logger.error(f"Error executing label tool: {e}\n{get_labels_query}")
            raise _tool_error_json("Unexpected Error", details=str(e))

    @mcp.resource(
        "resource://neo4j/labels/{label}",
        name="check_label_existance",
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
RETURN id(n) AS id, labels(n) AS labels, properties(n) AS properties
"""

        try:
            results = await _execute_read_query(
                get_labels_by_name_query, {"targetLabel": label}
            )
            sanitized_results = [_node_from_row(row) for row in results]
            logger.debug(f"Label resource query returned {len(sanitized_results)} rows")
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
            raise ResourceError(f"Error: {e}\n{get_labels_by_name_query}\n{label}")

    @mcp.resource(
        "resource://neo4j/refarchi",
        name="neo4j_refarchi",
        title="Neo4j Reference Architecture",
        description="Reference guidance for label lookup and schema access.",
        mime_type="text/plain",
    )
    async def neo4j_refarchi() -> str:
        """Provide reference guidance for label lookup and schema access."""
        return (
            "Reference usage guide for MCP Neo4j tools and resources.\n\n"
            "1) List all labels:\n"
            "Use the tool `get_db_labels` to fetch every label in the database.\n\n"
            "2) Check label existence and fetch nodes:\n"
            "Read `resource://neo4j/labels/{label}` (name: check_label_existance).\n"
            "The lookup is case-insensitive and returns a JSON array of nodes.\n\n"
            "Example MCP calls:\n"
            '- tools/call: {"name": "get_db_labels", "arguments": {}}\n'
            '- resources/read: {"uri": "resource://neo4j/labels/Person"}\n\n'
            "All tools are read-only, idempotent, and safe to call repeatedly."
        )

    @mcp.prompt(title="Neo4j Reference Architecture")
    def neo4j_refarchi_prompt() -> str:
        """Prompt helper to instruct agents to read the reference resource."""
        return (
            "Before proceeding, read the reference guide:\n"
            "resource://neo4j/refarchi\n\n"
            "Follow its instructions for label checks and schema inspection."
        )

    @mcp.tool(
        name=namespace_prefix + "get_coreConcept",
        annotations=ToolAnnotations(
            title="Get coreConcept Nodes",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def get_core_concept_nodes() -> ToolResult:
        """Return coreConcept nodes as JSON objects."""

        get_core_concept_query = (
            "MATCH (n:coreConcept) "
            "RETURN id(n) AS id, labels(n) AS labels, properties(n) AS properties"
        )

        try:
            results = await _execute_read_query(get_core_concept_query)
            sanitized_results = [_node_from_row(row) for row in results]
            logger.debug(f"Core concept tool returned {len(sanitized_results)} rows")
            return _tool_result(sanitized_results)

        except Neo4jError as e:
            logger.error(
                "Neo4j Error executing core concept tool: "
                f"{e}\n{get_core_concept_query}"
            )
            raise _tool_error_json(
                "Neo4j Error", details=str(e), query=get_core_concept_query
            )

        except Exception as e:
            logger.error(
                f"Error executing core concept tool: {e}\n{get_core_concept_query}"
            )
            raise _tool_error_json("Unexpected Error", details=str(e))

    @mcp.tool(
        name=namespace_prefix + "neo4j_schema_snapshot",
        annotations=ToolAnnotations(
            title="Neo4j Schema Snapshot",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def neo4j_schema_snapshot(
        sample_size: int = Field(
            default=config_sample_size,
            description=(
                "Sample size used for APOC schema inference. Use -1 for full scan."
            ),
        ),
    ) -> ToolResult:
        """Return a schema snapshot using APOC."""
        schema = await _read_schema(sample_size)
        return _tool_result(schema)

    @mcp.tool(
        name=namespace_prefix + TOOL_NAMES["read"],
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
    ) -> ToolResult:
        """Execute a read Cypher query on the neo4j database."""

        if _is_write_query(query):
            raise ValueError("Only MATCH queries are allowed for read-query")

        try:
            query_obj = Query(cast(LiteralString, query), timeout=float(read_timeout))
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

            return ToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=cast(LiteralString, results_json_str),
                    )
                ]
            )

        except Neo4jError as e:
            logger.error(f"Neo4j Error executing read query: {e}\n{query}\n{params}")
            raise ToolError(f"Neo4j Error: {e}\n{query}\n{params}")

        except Exception as e:
            logger.error(f"Error executing read query: {e}\n{query}\n{params}")
            raise ToolError(f"Error: {e}\n{query}\n{params}")

    @mcp.tool(
        name=namespace_prefix + TOOL_NAMES["write"],
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
    ) -> ToolResult:
        """Execute a write Cypher query on the neo4j database."""

        if not _is_write_query(query):
            raise ValueError("Only write queries are allowed for write-query")

        try:
            query_obj = Query(cast(LiteralString, query))
            _, summary, _ = await neo4j_driver.execute_query(
                query_obj,
                parameters_=params,
                routing_control=RoutingControl.WRITE,
                database_=database,
            )

            counters_json_str = json.dumps(summary.counters.__dict__, default=str)

            logger.debug(f"Write query affected {counters_json_str}")

            return ToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=cast(LiteralString, counters_json_str),
                    )
                ]
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
    host: Optional[str] = "127.0.0.1",
    port: Optional[int] = 8000,
    path: Optional[str] = "/mcp/",
    allow_origins: list[str] = [],
    allowed_hosts: list[str] = [],
    read_timeout: int = 30,
    token_limit: Optional[int] = None,
    read_only: bool = False,
    schema_sample_size: Optional[
        int
    ] = None,  # this is known as the config_sample_size in the create_mcp_server function
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

    try:
        mcp = create_mcp_server(
            neo4j_driver,
            database,
            namespace,
            read_timeout,
            token_limit,
            read_only,
            schema_sample_size if schema_sample_size is not None else 1000,
        )
    except Exception as exc:
        message = _format_startup_error(exc)
        logger.error(message)
        await neo4j_driver.close()
        raise RuntimeError(message) from exc

    # Run the server with the specified transport
    match transport:
        case "http":
            http_host = host or "127.0.0.1"
            http_port = port or 8000
            http_path = path or "/mcp/"
            logger.info(
                f"Running Neo4j Cypher MCP Server with HTTP transport on {http_host}:{http_port}..."
            )
            await mcp.run_http_async(
                host=http_host,
                port=http_port,
                path=http_path,
                middleware=custom_middleware,
            )
        case "stdio":
            logger.info("Running Neo4j Cypher MCP Server with stdio transport...")
            await mcp.run_stdio_async()
        case "sse":
            sse_host = host or "127.0.0.1"
            sse_port = port or 8000
            sse_path = path or "/mcp/"
            logger.info(
                f"Running Neo4j Cypher MCP Server with SSE transport on {sse_host}:{sse_port}..."
            )
            await mcp.run_http_async(
                host=sse_host,
                port=sse_port,
                path=sse_path,
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
    raise SystemExit("Use the mcp-neo4j-cypher entrypoint to start the server.")

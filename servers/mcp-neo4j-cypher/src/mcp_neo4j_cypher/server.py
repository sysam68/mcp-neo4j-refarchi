import builtins
import inspect
import json
import logging
import os
import re
from http import HTTPStatus
from typing import Any, Literal, LiteralString, Optional, cast

import anyio
import httpx
from fastmcp.exceptions import ResourceError, ToolError
from fastmcp.resources.base import ResourceContent, ResourceResult
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
                            create_session_message = self._create_session_message
                            protocol_version = None
                            try:
                                signature = inspect.signature(create_session_message)
                            except (TypeError, ValueError):
                                signature = None

                            if signature and "protocol_version" in signature.parameters:
                                protocol_version = request.headers.get(
                                    streamable_http.MCP_PROTOCOL_VERSION_HEADER
                                )
                                if not protocol_version:
                                    protocol_version = getattr(
                                        streamable_http,
                                        "DEFAULT_NEGOTIATED_VERSION",
                                        None,
                                    )
                                if not protocol_version:
                                    protocol_version = "2025-03-26"
                                session_message = create_session_message(
                                    message,
                                    request,
                                    request_id,
                                    protocol_version,
                                )
                            else:
                                session_message = create_session_message(
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
    database: Optional[str] = None,
    namespace: str = "",
    read_timeout: int = 30,
    token_limit: Optional[int] = None,
    read_only: bool = False,
    config_sample_size: int = 1000,
    embedding_base_url: Optional[str] = None,
    embedding_model: Optional[str] = None,
    embedding_timeout: int = 30,
) -> FastMCP:
    _patch_streamable_http_disconnect_handling()
    _patch_mcp_send_log_message()

    mcp: FastMCP = FastMCP("mcp-neo4j-cypher")

    namespace_prefix = _format_namespace(namespace)
    allow_writes = not read_only

    resolved_database = database or os.getenv("NEO4J_DATABASE", "neo4j")
    resolved_embedding_base_url = embedding_base_url or os.getenv(
        "NEO4J_EMBEDDING_BASE_URL", "http://llm.shared.mpn:11434"
    )
    resolved_embedding_model = embedding_model or os.getenv(
        "NEO4J_EMBEDDING_MODEL", "nomic-embed-text-v2-moe:latest"
    )

    async def _list_vector_indexes() -> list[dict[str, Any]]:
        cypher = """
SHOW INDEXES
YIELD name, type, entityType, labelsOrTypes, properties, options
WHERE type = 'VECTOR'
RETURN name, entityType, labelsOrTypes, properties, options
ORDER BY name
"""
        return await _execute_read_query(cypher)

    async def _execute_read_query(
        query: str, params: Optional[dict[str, Any]] = None
    ) -> list[dict[str, Any]]:
        query_obj = Query(cast(LiteralString, query), timeout=float(read_timeout))
        return await neo4j_driver.execute_query(
            query_obj,
            parameters_=params or {},
            routing_control=RoutingControl.READ,
            database_=resolved_database,
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

    def _resource_result(
        uri: str,
        payload: Any,
        mime_type: str,
    ) -> ResourceResult:
        return ResourceResult(
            contents=[
                ResourceContent(
                    payload,
                    mime_type=mime_type,
                    meta={"uri": uri},
                )
            ]
        )

    async def _get_embedding_from_ollama(text: str) -> list[float]:
        url = f"{resolved_embedding_base_url.rstrip('/')}/api/embeddings"
        payload = {"model": resolved_embedding_model, "prompt": text}

        try:
            async with httpx.AsyncClient(timeout=embedding_timeout) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as e:
            logger.error(f"Ollama embedding request failed: {e}")
            raise ToolError(f"Ollama embedding request failed: {e}")
        except ValueError as e:
            logger.error(f"Invalid Ollama embedding response: {e}")
            raise ToolError(f"Invalid Ollama embedding response: {e}")

        if isinstance(data, dict):
            if isinstance(data.get("embedding"), list):
                return cast(list[float], data["embedding"])
            embeddings = data.get("embeddings")
            if (
                isinstance(embeddings, list)
                and embeddings
                and isinstance(embeddings[0], list)
            ):
                return cast(list[float], embeddings[0])

        raise ToolError("Unexpected Ollama embedding response format.")

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

    def _modelization_node_from_row(row: dict[str, Any]) -> dict[str, Any]:
        labels = row.get("labels") or []
        if isinstance(labels, (set, tuple)):
            labels = list(labels)
        return {
            "id": row.get("id"),
            "label": row.get("label"),
            "name": row.get("name"),
            "modelizationClass": row.get("modelizationClass"),
            "labels": _value_sanitize(labels),
        }

    def _parse_node_id(node_id: str) -> str:
        parsed_node_id = str(node_id).strip()
        if not parsed_node_id:
            raise ResourceError("Invalid node id: empty value")
        return parsed_node_id

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
        name=namespace_prefix + "get_all_labels",
        annotations=ToolAnnotations(
            title="Get Neo4j Labels",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def get_all_labels() -> ToolResult:
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

    @mcp.tool(
        name=namespace_prefix + "get_node_by_element_id",
        annotations=ToolAnnotations(
            title="Get Node By Element ID",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def get_node_by_element_id(
        element_id: str = Field(
            ...,
            description="Neo4j elementId of the node to retrieve.",
        ),
    ) -> ToolResult:
        """Return one node by its Neo4j elementId."""

        parsed_element_id = _parse_node_id(element_id)
        get_node_by_element_id_query = """
MATCH (n)
WHERE elementId(n) = $elementId
RETURN elementId(n) AS id, labels(n) AS labels, properties(n) AS properties
LIMIT 1
"""

        try:
            results = await _execute_read_query(
                get_node_by_element_id_query,
                {"elementId": parsed_element_id},
            )
            payload = _node_from_row(results[0]) if results else None
            return _tool_result(payload)

        except Neo4jError as e:
            logger.error(
                "Neo4j Error executing get node by element id tool: "
                f"{e}\n{get_node_by_element_id_query}\n{parsed_element_id}"
            )
            raise _tool_error_json(
                "Neo4j Error",
                details=str(e),
                query=get_node_by_element_id_query,
                element_id=parsed_element_id,
            )

        except Exception as e:
            logger.error(
                "Error executing get node by element id tool: "
                f"{e}\n{get_node_by_element_id_query}\n{parsed_element_id}"
            )
            raise _tool_error_json("Unexpected Error", details=str(e))

    @mcp.tool(
        name=namespace_prefix + "get_nodes_by_element_ids",
        annotations=ToolAnnotations(
            title="Get Nodes By Element IDs",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def get_nodes_by_element_ids(
        element_ids: list[str] = Field(
            ...,
            description="List of Neo4j elementIds to retrieve.",
        ),
    ) -> ToolResult:
        """Return multiple nodes by their Neo4j elementIds."""

        parsed_element_ids = [_parse_node_id(element_id) for element_id in element_ids]
        get_nodes_by_element_ids_query = """
MATCH (n)
WHERE elementId(n) IN $elementIds
RETURN elementId(n) AS id, labels(n) AS labels, properties(n) AS properties
"""

        try:
            results = await _execute_read_query(
                get_nodes_by_element_ids_query,
                {"elementIds": parsed_element_ids},
            )
            nodes_by_id = {
                str(row.get("id")): _node_from_row(row)
                for row in results
            }
            payload = [
                nodes_by_id.get(element_id)
                for element_id in parsed_element_ids
            ]
            return _tool_result(payload)

        except Neo4jError as e:
            logger.error(
                "Neo4j Error executing get nodes by element ids tool: "
                f"{e}\n{get_nodes_by_element_ids_query}\n{parsed_element_ids}"
            )
            raise _tool_error_json(
                "Neo4j Error",
                details=str(e),
                query=get_nodes_by_element_ids_query,
                element_ids=parsed_element_ids,
            )

        except Exception as e:
            logger.error(
                "Error executing get nodes by element ids tool: "
                f"{e}\n{get_nodes_by_element_ids_query}\n{parsed_element_ids}"
            )
            raise _tool_error_json("Unexpected Error", details=str(e))

    async def _fetch_label_nodes(label: str) -> list[dict[str, Any]]:
        get_labels_by_name_query = """
WITH toLower($targetLabel) AS targetLabelLower
MATCH (n)
WHERE any(label IN labels(n)
          WHERE toLower(label) = targetLabelLower)
RETURN elementId(n) AS id, labels(n) AS labels, properties(n) AS properties
"""
        results = await _execute_read_query(
            get_labels_by_name_query, {"targetLabel": label}
        )
        return [_node_from_row(row) for row in results]

    async def _fetch_related_nodes(node_id: str) -> list[dict[str, Any]]:
        related_nodes_query = """
MATCH (source)-[r]-(target)
WHERE elementId(source) = $nodeId
RETURN
    type(r) AS relationship_type,
    CASE
        WHEN startNode(r) = source THEN 'outgoing'
        ELSE 'incoming'
    END AS direction,
    properties(r) AS relationship_properties,
    elementId(target) AS id,
    labels(target) AS labels,
    properties(target) AS properties
ORDER BY relationship_type, id
"""
        results = await _execute_read_query(
            related_nodes_query,
            {"nodeId": node_id},
        )
        return [
            {
                "relationship_type": row.get("relationship_type"),
                "direction": row.get("direction"),
                "relationship_properties": _value_sanitize(
                    row.get("relationship_properties") or {}
                ),
                "node": _node_from_row(row),
            }
            for row in results
        ]

    async def _fetch_node_attributes(node_id: str) -> dict[str, Any] | None:
        node_attributes_query = """
MATCH (n)
WHERE elementId(n) = $nodeId
RETURN elementId(n) AS id, labels(n) AS labels, properties(n) AS properties
LIMIT 1
"""
        results = await _execute_read_query(
            node_attributes_query,
            {"nodeId": node_id},
        )
        return _node_from_row(results[0]) if results else None

    async def _fetch_modelization_context(node_id: str) -> dict[str, Any] | None:
        anchor_query = """
MATCH (n)
WHERE elementId(n) = $nodeId
RETURN
    elementId(n) AS id,
    labels(n) AS labels,
    coalesce(n.label, n.name, n.title, n.id, elementId(n)) AS label,
    n.name AS name,
    n.modelizationClass AS modelizationClass
LIMIT 1
"""
        outgoing_query = """
MATCH (source)-[r]->(target)
WHERE elementId(source) = $nodeId
RETURN
    type(r) AS relationship_type,
    elementId(target) AS id,
    labels(target) AS labels,
    coalesce(target.label, target.name, target.title, target.id, elementId(target)) AS label,
    target.name AS name,
    target.modelizationClass AS modelizationClass
ORDER BY relationship_type, id
"""
        incoming_query = """
MATCH (source)-[r]->(target)
WHERE elementId(target) = $nodeId
RETURN
    type(r) AS relationship_type,
    elementId(source) AS id,
    labels(source) AS labels,
    coalesce(source.label, source.name, source.title, source.id, elementId(source)) AS label,
    source.name AS name,
    source.modelizationClass AS modelizationClass
ORDER BY relationship_type, id
"""

        anchor_rows = await _execute_read_query(anchor_query, {"nodeId": node_id})
        if not anchor_rows:
            return None

        outgoing_rows = await _execute_read_query(outgoing_query, {"nodeId": node_id})
        incoming_rows = await _execute_read_query(incoming_query, {"nodeId": node_id})

        return {
            "node": _modelization_node_from_row(anchor_rows[0]),
            "outgoing_relationships": [
                {
                    "relationship_type": row.get("relationship_type"),
                    "direction": "outgoing",
                    "target": _modelization_node_from_row(row),
                }
                for row in outgoing_rows
            ],
            "incoming_relationships": [
                {
                    "relationship_type": row.get("relationship_type"),
                    "direction": "incoming",
                    "source": _modelization_node_from_row(row),
                }
                for row in incoming_rows
            ],
        }

    async def _fetch_specialization_parents(node_id: str) -> list[dict[str, Any]]:
        specialization_parents_query = """
MATCH path = (child)-[:SpecializationRelationships*1..]->(parent)
WHERE elementId(child) = $nodeId
WITH parent, min(length(path)) AS depth
RETURN
    depth,
    elementId(parent) AS id,
    labels(parent) AS labels,
    properties(parent) AS properties
ORDER BY depth, id
"""
        results = await _execute_read_query(
            specialization_parents_query,
            {"nodeId": node_id},
        )
        return [
            {
                "depth": row.get("depth"),
                "relationship_type": "SpecializationRelationships",
                "node": _node_from_row(row),
            }
            for row in results
        ]

    async def _fetch_specialization_children(node_id: str) -> list[dict[str, Any]]:
        specialization_children_query = """
MATCH path = (child)-[:SpecializationRelationships*1..]->(parent)
WHERE elementId(parent) = $nodeId
WITH child, min(length(path)) AS depth
RETURN
    depth,
    elementId(child) AS id,
    labels(child) AS labels,
    properties(child) AS properties
ORDER BY depth, id
"""
        results = await _execute_read_query(
            specialization_children_query,
            {"nodeId": node_id},
        )
        return [
            {
                "depth": row.get("depth"),
                "relationship_type": "SpecializationRelationships",
                "node": _node_from_row(row),
            }
            for row in results
        ]

    @mcp.tool(
        name=namespace_prefix + "find_nodes_by_label",
        annotations=ToolAnnotations(
            title="Find Nodes By Label",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def find_nodes_by_label(
        label: str = Field(
            ...,
            description="Label name to match (case-insensitive).",
        ),
    ) -> ToolResult:
        """Return nodes matching a label name, case-insensitively."""

        try:
            return _tool_result(await _fetch_label_nodes(label))
        except Neo4jError as e:
            raise _tool_error_json("Neo4j Error", details=str(e), label=label)
        except Exception as e:
            raise _tool_error_json("Unexpected Error", details=str(e), label=label)

    @mcp.tool(
        name=namespace_prefix + "get_directly_related_nodes_by_element_id",
        annotations=ToolAnnotations(
            title="Get Directly Related Nodes By Element ID",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def get_directly_related_nodes_by_element_id(
        element_id: str = Field(
            ...,
            description="Neo4j elementId used as the anchor for direct relationship lookup.",
        ),
    ) -> ToolResult:
        """Return all nodes directly related to the given Neo4j elementId."""

        parsed_element_id = _parse_node_id(element_id)
        try:
            return _tool_result(await _fetch_related_nodes(parsed_element_id))
        except Neo4jError as e:
            raise _tool_error_json(
                "Neo4j Error", details=str(e), element_id=parsed_element_id
            )
        except Exception as e:
            raise _tool_error_json(
                "Unexpected Error", details=str(e), element_id=parsed_element_id
            )

    @mcp.tool(
        name=namespace_prefix + "get_node_attributes_by_element_id",
        annotations=ToolAnnotations(
            title="Get Node Attributes By Element ID",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def get_node_attributes_by_element_id(
        element_id: str = Field(
            ...,
            description="Neo4j elementId used to fetch labels and properties.",
        ),
    ) -> ToolResult:
        """Return labels and properties for a Neo4j elementId."""

        parsed_element_id = _parse_node_id(element_id)
        try:
            return _tool_result(await _fetch_node_attributes(parsed_element_id))
        except Neo4jError as e:
            raise _tool_error_json(
                "Neo4j Error", details=str(e), element_id=parsed_element_id
            )
        except Exception as e:
            raise _tool_error_json(
                "Unexpected Error", details=str(e), element_id=parsed_element_id
            )

    @mcp.tool(
        name=namespace_prefix + "get_modelization_context_by_element_id",
        annotations=ToolAnnotations(
            title="Get Modelization Context By Element ID",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def get_modelization_context_by_element_id(
        element_id: str = Field(
            ...,
            description=(
                "Neo4j elementId used to fetch modelizationClass plus first-level "
                "incoming and outgoing relationships."
            ),
        ),
    ) -> ToolResult:
        """Return a PlantUML-friendly JSON view of a node and its direct graph context."""

        parsed_element_id = _parse_node_id(element_id)
        try:
            return _tool_result(await _fetch_modelization_context(parsed_element_id))
        except Neo4jError as e:
            raise _tool_error_json(
                "Neo4j Error", details=str(e), element_id=parsed_element_id
            )
        except Exception as e:
            raise _tool_error_json(
                "Unexpected Error", details=str(e), element_id=parsed_element_id
            )

    @mcp.tool(
        name=namespace_prefix + "get_specialization_parents_by_element_id",
        annotations=ToolAnnotations(
            title="Get Specialization Parents By Element ID",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def get_specialization_parents_by_element_id(
        element_id: str = Field(
            ...,
            description="Neo4j elementId used as the child node in the specialization chain.",
        ),
    ) -> ToolResult:
        """Return all parent nodes in the specialization chain for a Neo4j elementId."""

        parsed_element_id = _parse_node_id(element_id)
        try:
            return _tool_result(await _fetch_specialization_parents(parsed_element_id))
        except Neo4jError as e:
            raise _tool_error_json(
                "Neo4j Error", details=str(e), element_id=parsed_element_id
            )
        except Exception as e:
            raise _tool_error_json(
                "Unexpected Error", details=str(e), element_id=parsed_element_id
            )

    @mcp.tool(
        name=namespace_prefix + "get_specialization_children_by_element_id",
        annotations=ToolAnnotations(
            title="Get Specialization Children By Element ID",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def get_specialization_children_by_element_id(
        element_id: str = Field(
            ...,
            description="Neo4j elementId used as the parent node in the specialization chain.",
        ),
    ) -> ToolResult:
        """Return all child nodes in the specialization chain for a Neo4j elementId."""

        parsed_element_id = _parse_node_id(element_id)
        try:
            return _tool_result(await _fetch_specialization_children(parsed_element_id))
        except Neo4jError as e:
            raise _tool_error_json(
                "Neo4j Error", details=str(e), element_id=parsed_element_id
            )
        except Exception as e:
            raise _tool_error_json(
                "Unexpected Error", details=str(e), element_id=parsed_element_id
            )

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
    ) -> ResourceResult:
        """Find nodes that match a label name, case-insensitively."""

        try:
            sanitized_results = await _fetch_label_nodes(label)
            logger.debug(f"Label resource query returned {len(sanitized_results)} rows")
            return _resource_result(
                f"resource://neo4j/labels/{label}",
                sanitized_results,
                "application/json",
            )

        except Neo4jError as e:
            raise ResourceError(f"Neo4j Error: {e}\n{label}")

        except Exception as e:
            raise ResourceError(f"Error: {e}\n{label}")

    @mcp.resource(
        "resource://neo4j/nodes/{node_id}/related-nodes",
        name="get_directly_related_nodes",
        title="Directly Related Nodes",
        description="Nodes directly connected to the given node id.",
        mime_type="application/json",
    )
    async def neo4j_related_nodes(
        node_id: str = Field(
            ...,
            description="Node id used as the anchor for direct relationship lookup.",
        ),
    ) -> ResourceResult:
        """Return all nodes directly related to the given node id."""

        parsed_node_id = _parse_node_id(node_id)

        try:
            payload = await _fetch_related_nodes(parsed_node_id)
            return _resource_result(
                f"resource://neo4j/nodes/{parsed_node_id}/related-nodes",
                payload,
                "application/json",
            )
        except Neo4jError as exc:
            raise ResourceError(f"Neo4j Error: {exc}\n{parsed_node_id}")
        except Exception as exc:
            raise ResourceError(f"Error: {exc}\n{parsed_node_id}")

    @mcp.resource(
        "resource://neo4j/nodes/{node_id}/attributes",
        name="get_node_attributes",
        title="Node Attributes",
        description="All attributes for the given node id.",
        mime_type="application/json",
    )
    async def neo4j_node_attributes(
        node_id: str = Field(
            ...,
            description="Node id used to fetch labels and properties.",
        ),
    ) -> ResourceResult:
        """Return labels and properties for a node id."""

        parsed_node_id = _parse_node_id(node_id)

        try:
            payload = await _fetch_node_attributes(parsed_node_id)
            return _resource_result(
                f"resource://neo4j/nodes/{parsed_node_id}/attributes",
                payload,
                "application/json",
            )
        except Neo4jError as exc:
            raise ResourceError(f"Neo4j Error: {exc}\n{parsed_node_id}")
        except Exception as exc:
            raise ResourceError(f"Error: {exc}\n{parsed_node_id}")

    @mcp.resource(
        "resource://neo4j/nodes/{node_id}/specialization-parents",
        name="get_specialization_parents",
        title="Specialization Parent Chain",
        description=(
            "Parent nodes reached through outgoing SpecializationRelationships."
        ),
        mime_type="application/json",
    )
    async def neo4j_specialization_parents(
        node_id: str = Field(
            ...,
            description="Node id used as the child node in the specialization chain.",
        ),
    ) -> ResourceResult:
        """Return all parent nodes in the specialization chain for a node id."""

        parsed_node_id = _parse_node_id(node_id)

        try:
            payload = await _fetch_specialization_parents(parsed_node_id)
            return _resource_result(
                f"resource://neo4j/nodes/{parsed_node_id}/specialization-parents",
                payload,
                "application/json",
            )
        except Neo4jError as exc:
            raise ResourceError(f"Neo4j Error: {exc}\n{parsed_node_id}")
        except Exception as exc:
            raise ResourceError(f"Error: {exc}\n{parsed_node_id}")

    @mcp.resource(
        "resource://neo4j/nodes/{node_id}/specialization-children",
        name="get_specialization_children",
        title="Specialization Child Chain",
        description=(
            "Child nodes reached through incoming SpecializationRelationships."
        ),
        mime_type="application/json",
    )
    async def neo4j_specialization_children(
        node_id: str = Field(
            ...,
            description="Node id used as the parent node in the specialization chain.",
        ),
    ) -> ResourceResult:
        """Return all child nodes in the specialization chain for a node id."""

        parsed_node_id = _parse_node_id(node_id)

        try:
            payload = await _fetch_specialization_children(parsed_node_id)
            return _resource_result(
                f"resource://neo4j/nodes/{parsed_node_id}/specialization-children",
                payload,
                "application/json",
            )
        except Neo4jError as exc:
            raise ResourceError(f"Neo4j Error: {exc}\n{parsed_node_id}")
        except Exception as exc:
            raise ResourceError(f"Error: {exc}\n{parsed_node_id}")

    @mcp.resource(
        "resource://neo4j/refarchi",
        name="neo4j_refarchi",
        title="Neo4j Reference Architecture",
        description="Reference guidance for label lookup and schema access.",
        mime_type="text/plain",
    )
    async def neo4j_refarchi() -> str:
        """Provide reference guidance for label lookup and schema access."""
        return _resource_result(
            "resource://neo4j/refarchi",
            "Reference usage guide for MCP Neo4j tools and resources.\n\n"
            "1) List all labels:\n"
            "Use the tool `get_all_labels` to fetch every label in the database.\n\n"
            "2) Check label existence and fetch nodes:\n"
            "Read `resource://neo4j/labels/{label}` (name: check_label_existance).\n"
            "The lookup is case-insensitive and returns a JSON array of nodes.\n\n"
            "3) Inspect graph navigation around a node id:\n"
            "Read `resource://neo4j/nodes/{node_id}/related-nodes` to fetch all "
            "direct neighbors.\n"
            "Use the tool `get_modelization_context_by_element_id` to fetch a "
            "PlantUML-friendly JSON payload with `modelizationClass` and "
            "first-level incoming/outgoing relationships.\n"
            "Read `resource://neo4j/nodes/{node_id}/attributes` to fetch all "
            "labels and properties for a node.\n"
            "Read `resource://neo4j/nodes/{node_id}/specialization-parents` to "
            "walk the outgoing `SpecializationRelationships` chain.\n"
            "Read `resource://neo4j/nodes/{node_id}/specialization-children` to "
            "walk the incoming `SpecializationRelationships` chain.\n\n"
            "Example MCP calls:\n"
            '- tools/call: {"name": "get_all_labels", "arguments": {}}\n'
            '- tools/call: {"name": "get_modelization_context_by_element_id", "arguments": {"element_id": "4:abc"}}\n'
            '- resources/read: {"uri": "resource://neo4j/labels/Person"}\n\n'
            '- resources/read: {"uri": "resource://neo4j/nodes/42/related-nodes"}\n'
            '- resources/read: {"uri": "resource://neo4j/nodes/42/attributes"}\n'
            '- resources/read: {"uri": "resource://neo4j/nodes/42/specialization-parents"}\n'
            '- resources/read: {"uri": "resource://neo4j/nodes/42/specialization-children"}\n\n'
            "All tools are read-only, idempotent, and safe to call repeatedly.",
            "text/plain",
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
        name=namespace_prefix + "get_core_concept",
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
            "RETURN elementId(n) AS id, labels(n) AS labels, properties(n) AS properties"
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
                database_=resolved_database,
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
        name=namespace_prefix + "vector_search",
        annotations=ToolAnnotations(
            title="Neo4j Vector Search",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def vector_search(
        index_name: str = Field(..., description="Neo4j vector index name."),
        query_text: Optional[str] = Field(
            default=None,
            description="Query text to embed (optional if query_embedding provided).",
        ),
        query_embedding: Optional[list[float]] = Field(
            default=None,
            description="Query embedding vector (optional if query_text provided).",
        ),
        top_k: int = Field(
            default=10, description="Number of nearest neighbors to return."
        ),
        return_properties: Optional[list[str]] = Field(
            default=None,
            description="Node properties to include in results (defaults to name, documentation).",
        ),
    ) -> ToolResult:
        """Perform a vector search on Neo4j using a vector index."""

        if query_embedding is None and not query_text:
            raise ToolError(
                "vector_search requires either query_embedding or query_text."
            )

        if index_name == "default":
            available = await _list_vector_indexes()
            available_names = [
                row.get("name") for row in available if isinstance(row.get("name"), str)
            ]
            if len(available_names) == 1:
                index_name = cast(str, available_names[0])
            else:
                raise ToolError(
                    json.dumps(
                        {
                            "error": "Unknown vector index: default",
                            "available_vector_indexes": available_names,
                            "hint": "Provide index_name from available_vector_indexes or create a Neo4j VECTOR index.",
                        }
                    )
                )

        embedding = query_embedding
        if embedding is None:
            embedding = await _get_embedding_from_ollama(query_text or "")

        props = return_properties or ["name", "documentation"]

        cypher = """
CALL db.index.vector.queryNodes($index_name, $top_k, $embedding)
YIELD node, score
RETURN elementId(node) AS id,
       labels(node) AS labels,
       score AS score,
       [k IN $return_properties | {key: k, value: node[k]}] AS properties
"""

        try:
            results = await _execute_read_query(
                cypher,
                {
                    "index_name": index_name,
                    "top_k": top_k,
                    "embedding": embedding,
                    "return_properties": props,
                },
            )
            for row in results:
                pairs = row.get("properties")
                if isinstance(pairs, list):
                    row["properties"] = {
                        pair.get("key"): pair.get("value")
                        for pair in pairs
                        if isinstance(pair, dict) and pair.get("key") is not None
                    }
            sanitized_results = [_value_sanitize(row) for row in results]
            return _tool_result(sanitized_results)
        except Neo4jError as e:
            msg = str(e)
            if "There is no such vector schema index" in msg:
                available = await _list_vector_indexes()
                available_names = [
                    row.get("name")
                    for row in available
                    if isinstance(row.get("name"), str)
                ]
                raise ToolError(
                    json.dumps(
                        {
                            "error": "Vector index not found",
                            "index_name": index_name,
                            "available_vector_indexes": available_names,
                        }
                    )
                )
            logger.error(f"Neo4j Error executing vector search: {e}\n{cypher}")
            raise ToolError(f"Neo4j Error: {e}\n{cypher}")
        except Exception as e:
            logger.error(f"Error executing vector search: {e}\n{cypher}")
            raise ToolError(f"Error: {e}\n{cypher}")

    @mcp.tool(
        name=namespace_prefix + "get_index_list",
        annotations=ToolAnnotations(
            title="Neo4j Vector Index List",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def get_index_list(
        include_details: bool = Field(
            default=False,
            description="Include index details (labels/types, properties, options).",
        ),
    ) -> ToolResult:
        """List available Neo4j VECTOR indexes for vector_search."""

        indexes = await _list_vector_indexes()
        if include_details:
            sanitized = [_value_sanitize(row) for row in indexes]
            return _tool_result(sanitized)

        names = [
            row.get("name") for row in indexes if isinstance(row.get("name"), str)
        ]
        return _tool_result(names)

    if allow_writes:
        @mcp.tool(
            name=namespace_prefix + TOOL_NAMES["write"],
            annotations=ToolAnnotations(
                title="Write Neo4j Cypher",
                readOnlyHint=False,
                destructiveHint=True,
                idempotentHint=False,
                openWorldHint=True,
            ),
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
                    database_=resolved_database,
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
    transport: Literal["stdio", "sse", "http", "streamable-http"] = "stdio",
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
    embedding_base_url: Optional[str] = None,
    embedding_model: Optional[str] = None,
    embedding_timeout: int = 30,
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
            embedding_base_url,
            embedding_model,
            embedding_timeout,
        )
    except Exception as exc:
        message = _format_startup_error(exc)
        logger.error(message)
        await neo4j_driver.close()
        raise RuntimeError(message) from exc

    # Run the server with the specified transport
    match transport:
        case "http" | "streamable-http":
            http_host = host or "127.0.0.1"
            http_port = port or 8000
            http_path = path or "/mcp/"
            logger.info(
                f"Running Neo4j Cypher MCP Server with streamable HTTP transport on {http_host}:{http_port}..."
            )
            await mcp.run_http_async(
                host=http_host,
                port=http_port,
                path=http_path,
                middleware=custom_middleware,
                stateless_http=True,
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
                stateless_http=False,
            )
        case _:
            logger.error(
                f"Invalid transport: {transport} | Must be either 'stdio', 'sse', or 'streamable-http'"
            )
            raise ValueError(
                f"Invalid transport: {transport} | Must be either 'stdio', 'sse', or 'streamable-http'"
            )


if __name__ == "__main__":
    raise SystemExit("Use the mcp-neo4j-cypher entrypoint to start the server.")

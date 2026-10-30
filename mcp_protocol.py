"""Small stdlib helpers for the MCP JSON-RPC protocol eras we implement."""

from __future__ import annotations


MODERN_VERSION = "2026-07-28"
LEGACY_VERSIONS = ("2025-11-25", "2025-06-18")
SUPPORTED_VERSIONS = (MODERN_VERSION, *LEGACY_VERSIONS)
SERVER_NAME = "agent-telemetry"
SERVER_VERSION = "2.2.0"

JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
MCP_UNSUPPORTED_PROTOCOL_VERSION = -32022


class ProtocolError(Exception):
    def __init__(self, code: int, message: str, data=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def valid_id(value) -> bool:
    return (isinstance(value, str) or type(value) is int) and value is not None


def valid_message_id(message):
    if not isinstance(message, dict) or "id" not in message:
        return False
    return valid_id(message.get("id"))


def valid_notification(message) -> bool:
    """Return whether a JSON-RPC 2.0 message is a valid one-way notification."""
    if not isinstance(message, dict) or "id" in message or message.get("jsonrpc") != "2.0":
        return False
    method = message.get("method")
    if not isinstance(method, str) or not method:
        return False
    params = message.get("params")
    return "params" not in message or isinstance(params, (dict, list))


def error_response(request_id, code: int, message: str, data=None) -> dict:
    error = {"code": int(code), "message": str(message)}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def result_response(request_id, result: dict, *, modern: bool) -> dict:
    result = dict(result)
    if modern:
        result["resultType"] = "complete"
        meta = result.get("_meta")
        meta = dict(meta) if isinstance(meta, dict) else {}
        meta["io.modelcontextprotocol/serverInfo"] = {
            "name": SERVER_NAME,
            "version": SERVER_VERSION,
        }
        result["_meta"] = meta
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def validate_modern_metadata(params) -> str:
    """Return a valid version or raise an MCP-standard protocol error."""
    if not isinstance(params, dict):
        raise ProtocolError(JSONRPC_INVALID_PARAMS, "Request parameters must include MCP metadata.")
    meta = params.get("_meta")
    if not isinstance(meta, dict):
        raise ProtocolError(JSONRPC_INVALID_PARAMS, "Request metadata is missing.")
    version = meta.get("io.modelcontextprotocol/protocolVersion")
    capabilities = meta.get("io.modelcontextprotocol/clientCapabilities")
    if not isinstance(version, str) or not isinstance(capabilities, dict):
        raise ProtocolError(JSONRPC_INVALID_PARAMS, "Request metadata must include protocolVersion and clientCapabilities.")
    if version != MODERN_VERSION:
        raise ProtocolError(
            MCP_UNSUPPORTED_PROTOCOL_VERSION,
            "Unsupported protocol version.",
            {"supported": [MODERN_VERSION], "requested": version},
        )
    return version


def legacy_initialize_result(requested_version):
    """Negotiate a version that this process actually implements."""
    if not isinstance(requested_version, str):
        raise ProtocolError(JSONRPC_INVALID_PARAMS, "initialize requires a protocolVersion string.")
    if requested_version in LEGACY_VERSIONS:
        return requested_version
    # JSON-RPC MCP version negotiation responds with the latest server version
    # it supports. The client can then disconnect if it cannot speak that era.
    return LEGACY_VERSIONS[0]

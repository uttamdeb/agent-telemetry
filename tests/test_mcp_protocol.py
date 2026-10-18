"""MCP tool schemas and JSON-RPC protocol contracts."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import mcp_protocol as protocol
import mcp_tools


class MCPToolContractTests(unittest.TestCase):
    def test_eight_ordered_tools_are_read_only_and_have_closed_object_schemas(self):
        self.assertEqual([tool["name"] for tool in mcp_tools.TOOLS], [
            "get_usage", "compare_usage", "get_insights", "list_sessions",
            "get_session_timeline", "get_data_health", "get_catalog", "get_subscription_limits",
        ])
        for tool in mcp_tools.TOOLS:
            with self.subTest(tool=tool["name"]):
                self.assertEqual(tool["inputSchema"]["type"], "object")
                self.assertIs(tool["inputSchema"]["additionalProperties"], False)
                self.assertEqual(tool["outputSchema"]["type"], "object")
                self.assertEqual(tool["annotations"], {
                    "readOnlyHint": True, "destructiveHint": False,
                    "openWorldHint": False, "idempotentHint": True,
                })
                args = set(tool["inputSchema"].get("properties", {}))
                self.assertNotIn("include_names", args)
                self.assertNotIn("include_peers", args)
                self.assertNotIn("token", args)
                self.assertNotIn("authorization", args)

    def test_usage_accepts_defaults_filters_and_totals_only(self):
        self.assertEqual(mcp_tools.validate_arguments("get_usage", {}), {})
        self.assertEqual(mcp_tools.validate_arguments("get_usage", {
            "group_by": [], "tools": ["Codex"], "limit": 500, "offset": 0,
        }), {"group_by": [], "tools": ["Codex"], "limit": 500, "offset": 0})

    def test_rejects_unknown_scope_fields_and_unsafe_values(self):
        for args in (
            {"include_names": True}, {"include_peers": True}, {"authorization": "x"},
            {"tools": ["Codex", "Codex"]}, {"tools": ["\nall"]},
            {"tools": ["x" * 201]}, {"tools": ["x"] * 101},
            {"limit": True}, {"limit": 2.0}, {"offset": -1}, {"exact_only": 1},
            {"group_by": ["model", "model"]}, {"group_by": ["source"]},
            {"period": "last_7_days", "from": "2026-10-01", "to": "2026-10-18"},
            {"from": "2026-10-01"}, {"from": "2026-10-18", "to": "2026-10-01"},
            {"from": "2026-02-30", "to": "2026-03-02"}, {"period": "all_time"},
        ):
            with self.subTest(args=args), self.assertRaises(mcp_tools.ToolArgumentError):
                mcp_tools.validate_arguments("get_usage", args)

    def test_comparison_and_insight_contracts_validate_required_metrics_and_ranges(self):
        self.assertEqual(mcp_tools.validate_arguments("compare_usage", {
            "metric": "tokens", "dimension": "project", "from": "2026-10-01", "to": "2026-10-07",
            "baseline_from": "2026-09-24", "baseline_to": "2026-09-30", "limit": 50,
        })["dimension"], "project")
        self.assertEqual(mcp_tools.validate_arguments("get_insights", {"period": "last_30_days"}),
                         {"period": "last_30_days"})
        for args in (
            {"metric": "tokens"},
            {"metric": "latency", "dimension": "project"},
            {"metric": "tokens", "dimension": "prompts"},
            {"metric": "tokens", "dimension": "model", "baseline_from": "2026-10-01"},
            {"metric": "tokens", "dimension": "tool", "baseline_from": "2026-10-03", "baseline_to": "2026-10-02"},
            {"metric": "tokens", "dimension": "device", "limit": True},
        ):
            with self.subTest(args=args), self.assertRaises(mcp_tools.ToolArgumentError):
                mcp_tools.validate_arguments("compare_usage", args)

    def test_session_selectors_are_opaque_and_timeline_date_bounds_are_paired(self):
        selector = "session-0123456789abcdef"
        self.assertEqual(mcp_tools.validate_arguments("get_session_timeline", {"session": selector}),
                         {"session": selector})
        with self.assertRaises(mcp_tools.ToolArgumentError):
            mcp_tools.validate_arguments("get_session_timeline", {"session": "native-thread-id"})
        with self.assertRaises(mcp_tools.ToolArgumentError):
            mcp_tools.validate_arguments("get_session_timeline", {"session": selector, "from": "2026-01-01"})

    def test_catalog_health_and_saved_quota_inputs_are_narrow(self):
        self.assertEqual(mcp_tools.validate_arguments("get_data_health", {}), {})
        self.assertEqual(mcp_tools.validate_arguments("get_catalog", {"projects": ["project-1"]}),
                         {"projects": ["project-1"]})
        self.assertEqual(mcp_tools.validate_arguments("get_subscription_limits", {"source": "claude"}),
                         {"source": "claude"})
        with self.assertRaises(mcp_tools.ToolArgumentError):
            mcp_tools.validate_arguments("get_subscription_limits", {"source": "openai", "refresh": True})


class MCPProtocolTests(unittest.TestCase):
    def test_only_reported_protocol_versions_are_selected_during_legacy_negotiation(self):
        self.assertEqual(protocol.SUPPORTED_VERSIONS,
                         ("2026-07-28", "2025-11-25", "2025-06-18"))
        for version in protocol.LEGACY_VERSIONS:
            self.assertEqual(protocol.legacy_initialize_result(version), version)
        self.assertEqual(protocol.legacy_initialize_result("2030-01-01"), "2025-11-25")
        with self.assertRaises(protocol.ProtocolError):
            protocol.legacy_initialize_result(None)

    def test_modern_metadata_is_required_and_version_mismatch_lists_supported_versions(self):
        with self.assertRaises(protocol.ProtocolError) as caught:
            protocol.validate_modern_metadata({})
        self.assertEqual(caught.exception.code, protocol.JSONRPC_INVALID_PARAMS)
        params = {"_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {},
        }}
        self.assertEqual(protocol.validate_modern_metadata(params), "2026-07-28")
        params["_meta"]["io.modelcontextprotocol/protocolVersion"] = "2025-11-25"
        with self.assertRaises(protocol.ProtocolError) as caught:
            protocol.validate_modern_metadata(params)
        self.assertEqual(caught.exception.code, protocol.MCP_UNSUPPORTED_PROTOCOL_VERSION)
        self.assertEqual(caught.exception.data["supported"], ["2026-07-28"])

    def test_jsonrpc_ids_reject_null_boolean_and_float(self):
        for value in (None, True, 1.5, []):
            self.assertFalse(protocol.valid_id(value))
        for value in ("request-1", 1, 0):
            self.assertTrue(protocol.valid_id(value))


if __name__ == "__main__":
    unittest.main()


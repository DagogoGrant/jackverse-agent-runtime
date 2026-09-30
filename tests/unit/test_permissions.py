"""Comprehensive unit tests for the Phase W3.5 Permissions and Action Authorization subsystem."""

import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

from prometheus_client import CollectorRegistry

from harness.agent.budget import ExecutionBudget
from harness.config import AppConfig, PermissionsConfig, PolicyRuleConfig, load_config
from harness.observability.logging import StructuredLogObserver
from harness.observability.metrics import PrometheusObserver
from harness.observability.tracing import OpenTelemetryObserver
from harness.permissions import (
    ALLOWLISTED_ARGUMENT_KEYS,
    AuthorizationResult,
    ConfirmationGrant,
    ConfirmationRecord,
    ConsoleConfirmationHandler,
    DeterministicConfirmationHandler,
    FILESYSTEM_TOOLS,
    PermissionDecision,
    PermissionManager,
    PermissionRequest,
    PolicyEngine,
    PolicyRule,
    RiskLevel,
    TOOL_SPECIFIC_SUMMARY_ALLOWLIST,
    canonical_tool_identity,
    compute_arguments_fingerprint,
    summarize_arguments,
)
from harness.runtime.context import ExecutionContext, execution_context_scope
from harness.runtime.events import (
    ConfirmationResolvedEvent,
    FailureCategory,
    LifecycleEvent,
    LifecycleEventBus,
    LifecycleEventType,
    PermissionEvaluatedEvent,
    ToolCallFinishedEvent,
    ToolCallStartedEvent,
    ToolCallStatus,
)
from harness.tools.base import ErrorCode, Tool, ToolResult, ToolSource, ToolSpec
from harness.tools.executor import ToolExecutor
from harness.tools.workspace import Workspace


class DummyTool:
    """Mock tool tracking whether execute() was physically called."""

    def __init__(
        self,
        name: str = "test_tool",
        source: ToolSource = ToolSource.BUILTIN,
        server_name: str | None = None,
        is_mutating: bool = False,
        return_error: bool = False,
    ) -> None:
        self.spec = ToolSpec(
            name=name,
            description="Test mock tool.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "cmd": {"type": "string"},
                },
            },
            is_mutating=is_mutating,
            source=source,
            server_name=server_name,
        )
        self.execute_called = False
        self.call_count = 0
        self.last_arguments: dict = {}
        self.return_error = return_error

    def execute(self, arguments: dict) -> ToolResult:
        self.execute_called = True
        self.call_count += 1
        self.last_arguments = dict(arguments)
        if self.return_error:
            return ToolResult(content="Tool failed internally.", is_error=True, error_code=ErrorCode.INTERNAL_ERROR)
        return ToolResult(content=f"Executed successfully with {len(arguments)} args.")


class RecordingObserver:
    """Synchronous test observer capturing lifecycle events."""

    def __init__(self) -> None:
        self.events: list[LifecycleEvent] = []

    def on_event(self, event: LifecycleEvent) -> None:
        self.events.append(event)


class TestPermissionsCore(unittest.TestCase):
    """Unit tests for canonical identity, fingerprinting, and argument redaction."""

    def test_canonical_tool_identity(self) -> None:
        # Builtin filesystem tools
        self.assertEqual(
            canonical_tool_identity(ToolSource.BUILTIN, None, "read_file"),
            "builtin:filesystem:read_file",
        )
        self.assertEqual(
            canonical_tool_identity(ToolSource.BUILTIN, None, "modify_file"),
            "builtin:filesystem:modify_file",
        )
        # Builtin arbitrary tool
        self.assertEqual(
            canonical_tool_identity(ToolSource.BUILTIN, None, "custom_action"),
            "builtin:builtin:custom_action",
        )
        # MCP tools
        self.assertEqual(
            canonical_tool_identity(ToolSource.MCP, "transport_service", "find_connection"),
            "mcp:transport_service:find_connection",
        )
        self.assertEqual(
            canonical_tool_identity(ToolSource.MCP, None, "raw_tool"),
            "mcp:mcp:raw_tool",
        )

    def test_compute_arguments_fingerprint_deterministic(self) -> None:
        # Key ordering must not affect SHA-256 fingerprint
        args1 = {"path": "foo/bar.txt", "content": "secret", "count": 42}
        args2 = {"count": 42, "content": "secret", "path": "foo/bar.txt"}
        fp1 = compute_arguments_fingerprint(args1)
        fp2 = compute_arguments_fingerprint(args2)
        self.assertEqual(fp1, fp2)
        self.assertEqual(len(fp1), 64)

        # Content change must alter fingerprint
        args3 = {"count": 42, "content": "modified", "path": "foo/bar.txt"}
        fp3 = compute_arguments_fingerprint(args3)
        self.assertNotEqual(fp1, fp3)

    def test_summarize_arguments_privacy_allowlist(self) -> None:
        raw_args = {
            "path": "workspace/data.csv",
            "directory": "workspace/subdir",
            "content": "A very secret prompt or file content that should never leak into logs or UI",
            "old_text": "secret_old",
            "new_text": "secret_new",
            "secret_key": "sk-1234567890",
            "query": "Munich to Passau",
            "unlisted_heavy_payload": "X" * 1000,
        }
        # modify_file: allows only path
        summary = summarize_arguments("modify_file", raw_args)
        self.assertEqual(summary, {"path": "workspace/data.csv"})
        self.assertNotIn("content", summary)
        self.assertNotIn("old_text", summary)
        self.assertNotIn("new_text", summary)
        self.assertNotIn("directory", summary)
        self.assertNotIn("query", summary)
        self.assertNotIn("secret_key", summary)
        self.assertNotIn("unlisted_heavy_payload", summary)

        # create_file / create_directory / read_file / list_directory: allows only path
        for fs_tool in ("create_file", "create_directory", "read_file", "list_directory"):
            self.assertEqual(summarize_arguments(fs_tool, raw_args), {"path": "workspace/data.csv"})

        # find_connection: allows origin, destination, departure_time
        conn_args = {
            "origin": "Munich",
            "destination": "Berlin",
            "departure_time": "08:00",
            "auth_token": "token-xyz",
            "query": "find fastest train",
        }
        conn_summary = summarize_arguments("find_connection", conn_args)
        self.assertEqual(conn_summary, {"departure_time": "08:00", "destination": "Berlin", "origin": "Munich"})

        # get_station_info: allows station, station_id
        station_args = {"station": "Berlin Hbf", "station_id": "8011160", "api_key": "secret"}
        station_summary = summarize_arguments("get_station_info", station_args)
        self.assertEqual(station_summary, {"station": "Berlin Hbf", "station_id": "8011160"})

        # Unlisted / unknown tools return empty summary
        self.assertEqual(summarize_arguments("unknown_tool", raw_args), {})
        self.assertEqual(summarize_arguments("search_files", {"query": "secret", "path": "test.txt"}), {})

    def test_confirmation_grant_alias(self) -> None:
        self.assertIs(ConfirmationGrant, ConfirmationRecord)


class TestPolicyEngine(unittest.TestCase):
    """Unit tests for declarative rules and deterministic precedence resolution."""

    def _make_request(
        self,
        tool_name: str = "read_file",
        source: ToolSource = ToolSource.BUILTIN,
        server_name: str | None = None,
        role: str = "orchestrator",
    ) -> PermissionRequest:
        canon_id = canonical_tool_identity(source, server_name, tool_name)
        return PermissionRequest(
            run_id="run-1",
            agent_id="agent-1",
            agent_role=role,
            delegation_depth=0,
            call_id="call-1",
            canonical_tool_identity=canon_id,
            tool_name=tool_name,
            tool_source=source,
            server_name=server_name,
            arguments={"path": "test.txt"},
            arguments_fingerprint="abc",
            arguments_summary={"path": "test.txt"},
        )

    def test_matching_constraints(self) -> None:
        rule_role = PolicyRule(name="role_rule", decision=PermissionDecision.ALLOW, role="specialist")
        rule_source = PolicyRule(name="mcp_only", decision=PermissionDecision.ALLOW, source=ToolSource.MCP)
        rule_server = PolicyRule(name="transport_server", decision=PermissionDecision.ALLOW, server_name="transport_service")
        rule_pattern = PolicyRule(name="read_pattern", decision=PermissionDecision.ALLOW, tool_pattern="read_*")

        engine = PolicyEngine(rules=[rule_role, rule_source, rule_server, rule_pattern], default_stance=PermissionDecision.DENY)

        # Role mismatch
        req_orch = self._make_request(tool_name="other", role="orchestrator")
        self.assertFalse(engine.matches(rule_role, req_orch))

        # Role match
        req_spec = self._make_request(tool_name="other", role="specialist")
        self.assertTrue(engine.matches(rule_role, req_spec))

        # Source match/mismatch
        self.assertFalse(engine.matches(rule_source, self._make_request(source=ToolSource.BUILTIN)))
        self.assertTrue(engine.matches(rule_source, self._make_request(source=ToolSource.MCP)))

        # Pattern match
        self.assertTrue(engine.matches(rule_pattern, self._make_request(tool_name="read_file")))
        self.assertFalse(engine.matches(rule_pattern, self._make_request(tool_name="write_file")))

    def test_precedence_priority_beats_decision(self) -> None:
        # High priority ALLOW (200) beats lower priority DENY (100)
        rule_deny = PolicyRule(name="deny_all", decision=PermissionDecision.DENY, priority=100, tool_pattern="*")
        rule_allow = PolicyRule(name="allow_read", decision=PermissionDecision.ALLOW, priority=200, tool_pattern="read_*")

        engine = PolicyEngine(rules=[rule_deny, rule_allow])
        req = self._make_request(tool_name="read_file")
        decision, matched, _ = engine.evaluate(req)
        self.assertEqual(decision, PermissionDecision.ALLOW)
        self.assertEqual(matched, "allow_read")

    def test_precedence_tie_break_deny_over_confirm_over_allow(self) -> None:
        # Equal priority (100): DENY > REQUIRE_CONFIRMATION > ALLOW
        rule_allow = PolicyRule(name="allow_rule", decision=PermissionDecision.ALLOW, priority=100, tool_pattern="*")
        rule_confirm = PolicyRule(name="confirm_rule", decision=PermissionDecision.REQUIRE_CONFIRMATION, priority=100, tool_pattern="*")
        rule_deny = PolicyRule(name="deny_rule", decision=PermissionDecision.DENY, priority=100, tool_pattern="*")

        engine = PolicyEngine(rules=[rule_allow, rule_confirm, rule_deny])
        req = self._make_request()
        decision, matched, _ = engine.evaluate(req)
        self.assertEqual(decision, PermissionDecision.DENY)
        self.assertEqual(matched, "deny_rule")

        # Without DENY, REQUIRE_CONFIRMATION beats ALLOW
        engine2 = PolicyEngine(rules=[rule_allow, rule_confirm])
        decision2, matched2, _ = engine2.evaluate(req)
        self.assertEqual(decision2, PermissionDecision.REQUIRE_CONFIRMATION)
        self.assertEqual(matched2, "confirm_rule")

    def test_precedence_tie_break_declaration_order(self) -> None:
        # Equal priority (100) and equal decision (ALLOW): first declared wins
        rule_first = PolicyRule(name="first_rule", decision=PermissionDecision.ALLOW, priority=100, tool_pattern="*")
        rule_second = PolicyRule(name="second_rule", decision=PermissionDecision.ALLOW, priority=100, tool_pattern="*")

        engine = PolicyEngine(rules=[rule_first, rule_second])
        req = self._make_request()
        decision, matched, _ = engine.evaluate(req)
        self.assertEqual(decision, PermissionDecision.ALLOW)
        self.assertEqual(matched, "first_rule")

    def test_closed_fallback_default_stance(self) -> None:
        engine = PolicyEngine(rules=[], default_stance=PermissionDecision.DENY)
        req = self._make_request(tool_name="arbitrary_unconfigured_tool")
        decision, matched, _ = engine.evaluate(req)
        self.assertEqual(decision, PermissionDecision.DENY)
        self.assertIsNone(matched)


class TestConfirmationHandlers(unittest.TestCase):
    """Unit tests for ConsoleConfirmationHandler and DeterministicConfirmationHandler."""

    def _sample_request(self, tool_name: str = "modify_file", call_id: str = "c-123") -> PermissionRequest:
        return PermissionRequest(
            run_id="r-1",
            agent_id="a-1",
            agent_role="orchestrator",
            delegation_depth=0,
            call_id=call_id,
            canonical_tool_identity=f"builtin:filesystem:{tool_name}",
            tool_name=tool_name,
            tool_source=ToolSource.BUILTIN,
            server_name=None,
            arguments={"path": "file.txt"},
            arguments_fingerprint="fp123",
            arguments_summary={"path": "file.txt"},
            resource_descriptor="/workspace/file.txt",
        )

    def test_console_confirmation_handler_yes(self) -> None:
        for yes_input in ("y\n", "Y\n", "yes\n", "YES \n"):
            stdin = io.StringIO(yes_input)
            stdout = io.StringIO()
            handler = ConsoleConfirmationHandler(stdin=stdin, stdout=stdout)
            result = handler.request_confirmation(self._sample_request())
            self.assertTrue(result)
            self.assertIn("[SECURITY CONFIRMATION REQUIRED]", stdout.getvalue())
            self.assertIn("builtin:filesystem:modify_file", stdout.getvalue())

    def test_console_confirmation_handler_reject(self) -> None:
        for no_input in ("n\n", "no\n", "\n", "random\n"):
            stdin = io.StringIO(no_input)
            stdout = io.StringIO()
            handler = ConsoleConfirmationHandler(stdin=stdin, stdout=stdout)
            result = handler.request_confirmation(self._sample_request())
            self.assertFalse(result)

    def test_console_confirmation_handler_eof_or_error(self) -> None:
        stdin = io.StringIO("")  # immediate EOF
        stdout = io.StringIO()
        handler = ConsoleConfirmationHandler(stdin=stdin, stdout=stdout)
        result = handler.request_confirmation(self._sample_request())
        self.assertFalse(result)

    def test_deterministic_confirmation_handler(self) -> None:
        handler = DeterministicConfirmationHandler(
            always_allow=False,
            responses={"c-approve": True, "read_file": True},
        )
        # Default fallback is always_allow (False)
        self.assertFalse(handler.request_confirmation(self._sample_request(call_id="c-default")))
        # Specific call_id override
        self.assertTrue(handler.request_confirmation(self._sample_request(call_id="c-approve")))
        # Specific tool_name override
        self.assertTrue(handler.request_confirmation(self._sample_request(tool_name="read_file", call_id="c-other")))
        # Captured all requests
        self.assertEqual(len(handler.requests), 3)

    def test_console_confirmation_prompt_privacy_no_secret_leakage(self) -> None:
        raw_args = {
            "path": "secret_config.json",
            "content": "SUPER_SECRET_PAYLOAD_TOKEN_ABC",
            "old_text": "PASSWORD_OLD_123",
            "new_text": "PASSWORD_NEW_456",
            "query": "SELECT secret_password FROM credentials",
            "api_key": "sk-live-9999999",
        }
        req = PermissionRequest(
            run_id="run-priv",
            agent_id="agent-priv",
            agent_role="orchestrator",
            delegation_depth=0,
            call_id="call-priv",
            canonical_tool_identity="builtin:filesystem:modify_file",
            tool_name="modify_file",
            tool_source=ToolSource.BUILTIN,
            server_name=None,
            arguments=raw_args,
            arguments_fingerprint=compute_arguments_fingerprint(raw_args),
            arguments_summary=summarize_arguments("modify_file", raw_args),
            resource_descriptor="/workspace/secret_config.json",
        )
        stdin = io.StringIO("y\n")
        stdout = io.StringIO()
        handler = ConsoleConfirmationHandler(stdin=stdin, stdout=stdout)
        approved = handler.request_confirmation(req)
        self.assertTrue(approved)
        output = stdout.getvalue()

        # Path and safe metadata are in the banner
        self.assertIn("builtin:filesystem:modify_file", output)
        self.assertIn("secret_config.json", output)
        # Sensitive payload, query, and api_key are strictly excluded
        self.assertNotIn("SUPER_SECRET_PAYLOAD_TOKEN_ABC", output)
        self.assertNotIn("PASSWORD_OLD_123", output)
        self.assertNotIn("PASSWORD_NEW_456", output)
        self.assertNotIn("SELECT secret_password FROM credentials", output)
        self.assertNotIn("sk-live-9999999", output)


class TestPermissionManager(unittest.TestCase):
    """Unit tests for PermissionManager, token verification, and closed fallback policy."""

    def test_allow_deny_and_confirmation_flows(self) -> None:
        rules = [
            PolicyRule(name="rule_allow", decision=PermissionDecision.ALLOW, tool_pattern="read_file"),
            PolicyRule(name="rule_deny", decision=PermissionDecision.DENY, tool_pattern="delete_file"),
            PolicyRule(name="rule_confirm", decision=PermissionDecision.REQUIRE_CONFIRMATION, tool_pattern="modify_file"),
        ]
        handler = DeterministicConfirmationHandler(always_allow=True)
        manager = PermissionManager(
            policy_engine=PolicyEngine(rules=rules, default_stance=PermissionDecision.DENY),
            confirmation_handler=handler,
        )

        # 1. ALLOW
        req_read = PermissionRequest(
            run_id="r1",
            agent_id="a1",
            agent_role="agent",
            delegation_depth=0,
            call_id="c1",
            canonical_tool_identity="builtin:filesystem:read_file",
            tool_name="read_file",
            tool_source=ToolSource.BUILTIN,
            server_name=None,
            arguments={"path": "a.txt"},
            arguments_fingerprint="fp1",
            arguments_summary={"path": "a.txt"},
        )
        res_read = manager.authorize(req_read)
        self.assertEqual(res_read.decision, PermissionDecision.ALLOW)
        self.assertEqual(res_read.matched_rule, "rule_allow")
        self.assertIsNone(res_read.confirmation_record)

        # 2. DENY
        req_del = PermissionRequest(
            run_id="r1",
            agent_id="a1",
            agent_role="agent",
            delegation_depth=0,
            call_id="c2",
            canonical_tool_identity="builtin:filesystem:delete_file",
            tool_name="delete_file",
            tool_source=ToolSource.BUILTIN,
            server_name=None,
            arguments={"path": "a.txt"},
            arguments_fingerprint="fp2",
            arguments_summary={"path": "a.txt"},
        )
        res_del = manager.authorize(req_del)
        self.assertEqual(res_del.decision, PermissionDecision.DENY)
        self.assertEqual(res_del.matched_rule, "rule_deny")

        # 3. REQUIRE_CONFIRMATION (approved)
        req_mod = PermissionRequest(
            run_id="r1",
            agent_id="a1",
            agent_role="agent",
            delegation_depth=0,
            call_id="c3",
            canonical_tool_identity="builtin:filesystem:modify_file",
            tool_name="modify_file",
            tool_source=ToolSource.BUILTIN,
            server_name=None,
            arguments={"path": "a.txt"},
            arguments_fingerprint="fp3",
            arguments_summary={"path": "a.txt"},
        )
        res_mod = manager.authorize(req_mod)
        self.assertEqual(res_mod.decision, PermissionDecision.ALLOW)
        self.assertIsNotNone(res_mod.confirmation_record)
        self.assertFalse(res_mod.confirmation_record.consumed)
        # Verify single-use consumption
        self.assertTrue(manager.consume_confirmation_token(res_mod.confirmation_record.confirmation_id))
        self.assertTrue(res_mod.confirmation_record.consumed)

        # 4. REQUIRE_CONFIRMATION (rejected)
        handler.always_allow = False
        res_mod_rej = manager.authorize(req_mod)
        self.assertEqual(res_mod_rej.decision, PermissionDecision.DENY)
        self.assertIn("denied by user confirmation", res_mod_rej.reason)

    def test_confirmation_token_binding_validation(self) -> None:
        manager = PermissionManager.create_default(
            confirmation_handler=DeterministicConfirmationHandler(always_allow=True)
        )
        req = PermissionRequest(
            run_id="run-1",
            agent_id="agent-1",
            agent_role="orchestrator",
            delegation_depth=0,
            call_id="call-1",
            canonical_tool_identity="builtin:filesystem:modify_file",
            tool_name="modify_file",
            tool_source=ToolSource.BUILTIN,
            server_name=None,
            arguments={"path": "data.txt", "content": "hello"},
            arguments_fingerprint=compute_arguments_fingerprint({"path": "data.txt", "content": "hello"}),
            arguments_summary={"path": "data.txt"},
        )
        res = manager.authorize(req)
        self.assertEqual(res.decision, PermissionDecision.ALLOW)
        record = res.confirmation_record
        self.assertIsNotNone(record)
        self.assertFalse(record.consumed)

        cid = record.confirmation_id

        # 1. Valid verification with exact matching principal, action, call, and fingerprint
        self.assertTrue(manager.verify_confirmation_token(cid, req))

        # 2. Cross-run misuse: token issued for run-1 cannot authorize run-2
        cross_run_req = PermissionRequest(
            run_id="run-2",
            agent_id=req.agent_id,
            agent_role=req.agent_role,
            delegation_depth=req.delegation_depth,
            call_id=req.call_id,
            canonical_tool_identity=req.canonical_tool_identity,
            tool_name=req.tool_name,
            tool_source=req.tool_source,
            server_name=req.server_name,
            arguments=req.arguments,
            arguments_fingerprint=req.arguments_fingerprint,
            arguments_summary=req.arguments_summary,
        )
        self.assertFalse(manager.verify_confirmation_token(cid, cross_run_req))

        # 3. Cross-call misuse: token issued for call-1 cannot authorize call-2
        cross_call_req = PermissionRequest(
            run_id=req.run_id,
            agent_id=req.agent_id,
            agent_role=req.agent_role,
            delegation_depth=req.delegation_depth,
            call_id="call-2",
            canonical_tool_identity=req.canonical_tool_identity,
            tool_name=req.tool_name,
            tool_source=req.tool_source,
            server_name=req.server_name,
            arguments=req.arguments,
            arguments_fingerprint=req.arguments_fingerprint,
            arguments_summary=req.arguments_summary,
        )
        self.assertFalse(manager.verify_confirmation_token(cid, cross_call_req))

        # 4. Cross-agent misuse: token issued for agent-1 cannot authorize agent-2
        cross_agent_req = PermissionRequest(
            run_id=req.run_id,
            agent_id="agent-2",
            agent_role=req.agent_role,
            delegation_depth=req.delegation_depth,
            call_id=req.call_id,
            canonical_tool_identity=req.canonical_tool_identity,
            tool_name=req.tool_name,
            tool_source=req.tool_source,
            server_name=req.server_name,
            arguments=req.arguments,
            arguments_fingerprint=req.arguments_fingerprint,
            arguments_summary=req.arguments_summary,
        )
        self.assertFalse(manager.verify_confirmation_token(cid, cross_agent_req))

        # 5. Cross-role misuse: token issued for orchestrator cannot authorize specialist
        cross_role_req = PermissionRequest(
            run_id=req.run_id,
            agent_id=req.agent_id,
            agent_role="specialist",
            delegation_depth=req.delegation_depth,
            call_id=req.call_id,
            canonical_tool_identity=req.canonical_tool_identity,
            tool_name=req.tool_name,
            tool_source=req.tool_source,
            server_name=req.server_name,
            arguments=req.arguments,
            arguments_fingerprint=req.arguments_fingerprint,
            arguments_summary=req.arguments_summary,
        )
        self.assertFalse(manager.verify_confirmation_token(cid, cross_role_req))

        # 6. Action identity tampering: token issued for modify_file cannot authorize delete_file
        tampered_tool_req = PermissionRequest(
            run_id=req.run_id,
            agent_id=req.agent_id,
            agent_role=req.agent_role,
            delegation_depth=req.delegation_depth,
            call_id=req.call_id,
            canonical_tool_identity="builtin:filesystem:delete_file",
            tool_name="delete_file",
            tool_source=req.tool_source,
            server_name=req.server_name,
            arguments=req.arguments,
            arguments_fingerprint=req.arguments_fingerprint,
            arguments_summary=req.arguments_summary,
        )
        self.assertFalse(manager.verify_confirmation_token(cid, tampered_tool_req))

        # 7. Anti-TOCTOU: arguments tampered after confirmation
        tampered_args = {"path": "data.txt", "content": "MALICIOUS_INJECTION"}
        tampered_req = PermissionRequest(
            run_id=req.run_id,
            agent_id=req.agent_id,
            agent_role=req.agent_role,
            delegation_depth=req.delegation_depth,
            call_id=req.call_id,
            canonical_tool_identity=req.canonical_tool_identity,
            tool_name=req.tool_name,
            tool_source=req.tool_source,
            server_name=req.server_name,
            arguments=tampered_args,
            arguments_fingerprint=compute_arguments_fingerprint(tampered_args),
            arguments_summary={"path": "data.txt"},
        )
        self.assertFalse(manager.verify_confirmation_token(cid, tampered_req))

        # 8. Single-use guarantee & anti-replay
        self.assertTrue(manager.consume_confirmation_token(cid))
        # After consumption, token verification must fail
        self.assertFalse(manager.verify_confirmation_token(cid, req))
        # Re-consuming already-consumed token must return False
        self.assertFalse(manager.consume_confirmation_token(cid))

        # 9. Nonexistent confirmation ID
        self.assertFalse(manager.verify_confirmation_token("nonexistent-id", req))

    def test_create_default_closed_fallback(self) -> None:
        manager = PermissionManager.create_default(
            confirmation_handler=DeterministicConfirmationHandler(always_allow=False)
        )

        def make_req(tool_name: str, source: ToolSource = ToolSource.BUILTIN, server: str | None = None) -> PermissionRequest:
            canon = canonical_tool_identity(source, server, tool_name)
            return PermissionRequest(
                run_id="r",
                agent_id="a",
                agent_role="agent",
                delegation_depth=0,
                call_id="c",
                canonical_tool_identity=canon,
                tool_name=tool_name,
                tool_source=source,
                server_name=server,
                arguments={},
                arguments_fingerprint="fp",
                arguments_summary={},
            )

        # Read tools allowed
        for read_tool in ("read_file", "list_directory", "search_files"):
            res = manager.authorize(make_req(read_tool))
            self.assertEqual(res.decision, PermissionDecision.ALLOW, f"Expected {read_tool} to be ALLOW")

        # Mutating tools require confirmation (rejected because always_allow=False)
        for mut_tool in ("create_file", "modify_file", "create_directory"):
            res = manager.authorize(make_req(mut_tool))
            self.assertEqual(res.decision, PermissionDecision.DENY, f"Expected {mut_tool} to be DENIED when confirmation rejected")

        # Transport MCP queries allowed
        res_mcp = manager.authorize(make_req("find_connection", source=ToolSource.MCP, server="transport_service"))
        self.assertEqual(res_mcp.decision, PermissionDecision.ALLOW)

        # Unmatched arbitrary action DENIED by closed default
        res_unmatched = manager.authorize(make_req("arbitrary_tool"))
        self.assertEqual(res_unmatched.decision, PermissionDecision.DENY)


class TestToolExecutorAuthorizationGate(unittest.TestCase):
    """Unit tests verifying that ToolExecutor enforces the mandatory authorization boundary."""

    def setUp(self) -> None:
        self.bus = LifecycleEventBus()
        self.observer = RecordingObserver()
        self.bus.subscribe(self.observer)

    def test_executor_always_has_permission_manager(self) -> None:
        executor = ToolExecutor()
        self.assertIsNotNone(executor.permission_manager)
        self.assertIsInstance(executor.permission_manager, PermissionManager)

    def test_executor_blocks_execution_on_deny(self) -> None:
        # Construct manager with rule denying 'test_tool'
        rule = PolicyRule(name="deny_test", decision=PermissionDecision.DENY, tool_pattern="test_tool")
        manager = PermissionManager(
            policy_engine=PolicyEngine(rules=[rule], default_stance=PermissionDecision.DENY),
            confirmation_handler=DeterministicConfirmationHandler(always_allow=True),
            event_bus=self.bus,
        )
        executor = ToolExecutor(event_bus=self.bus, permission_manager=manager)
        tool = DummyTool(name="test_tool")

        ctx = ExecutionContext(
            trace_id="0" * 32,
            run_id="1" * 32,
            root_run_id="1" * 32,
            parent_run_id=None,
            delegation_depth=0,
            agent_id="test-agent",
            agent_role="orchestrator",
            budget_limits=ExecutionBudget(max_steps=5),
        )

        with execution_context_scope(ctx):
            result = executor.execute(tool, {"path": "test.txt"})

        # Underlying tool execution must NOT have been called
        self.assertFalse(tool.execute_called)
        self.assertEqual(tool.call_count, 0)

        # Result must indicate permission denied
        self.assertTrue(result.is_error)
        self.assertEqual(result.error_code, ErrorCode.PERMISSION_DENIED)
        self.assertIn("Permission denied", result.content)

        # Telemetry events emitted
        perm_events = [e for e in self.observer.events if isinstance(e, PermissionEvaluatedEvent)]
        self.assertEqual(len(perm_events), 1)
        self.assertEqual(perm_events[0].decision, PermissionDecision.DENY)
        self.assertEqual(perm_events[0].matched_rule, "deny_test")

        finish_events = [e for e in self.observer.events if isinstance(e, ToolCallFinishedEvent)]
        self.assertEqual(len(finish_events), 1)
        self.assertEqual(finish_events[0].status, ToolCallStatus.PERMISSION_DENIED)
        self.assertEqual(finish_events[0].failure_category, FailureCategory.PERMISSION)
        self.assertEqual(finish_events[0].error_code, ErrorCode.PERMISSION_DENIED.value)

    def test_executor_confirmation_rejected_blocks_execution(self) -> None:
        rule = PolicyRule(name="confirm_test", decision=PermissionDecision.REQUIRE_CONFIRMATION, tool_pattern="test_tool")
        manager = PermissionManager(
            policy_engine=PolicyEngine(rules=[rule], default_stance=PermissionDecision.DENY),
            confirmation_handler=DeterministicConfirmationHandler(always_allow=False),
            event_bus=self.bus,
        )
        executor = ToolExecutor(event_bus=self.bus, permission_manager=manager)
        tool = DummyTool(name="test_tool")

        ctx = ExecutionContext(
            trace_id="0" * 32,
            run_id="1" * 32,
            root_run_id="1" * 32,
            parent_run_id=None,
            delegation_depth=0,
            agent_id="test-agent",
            agent_role="orchestrator",
            budget_limits=ExecutionBudget(max_steps=5),
        )

        with execution_context_scope(ctx):
            result = executor.execute(tool, {"path": "test.txt"})

        # Execution blocked
        self.assertFalse(tool.execute_called)
        self.assertTrue(result.is_error)
        self.assertEqual(result.error_code, ErrorCode.PERMISSION_DENIED)

        # Events emitted
        confirm_events = [e for e in self.observer.events if isinstance(e, ConfirmationResolvedEvent)]
        self.assertEqual(len(confirm_events), 1)
        self.assertFalse(confirm_events[0].approved)

    def test_executor_confirmation_approved_executes_tool(self) -> None:
        rule = PolicyRule(name="confirm_test", decision=PermissionDecision.REQUIRE_CONFIRMATION, tool_pattern="test_tool")
        manager = PermissionManager(
            policy_engine=PolicyEngine(rules=[rule], default_stance=PermissionDecision.DENY),
            confirmation_handler=DeterministicConfirmationHandler(always_allow=True),
            event_bus=self.bus,
        )
        executor = ToolExecutor(event_bus=self.bus, permission_manager=manager)
        tool = DummyTool(name="test_tool")

        ctx = ExecutionContext(
            trace_id="0" * 32,
            run_id="1" * 32,
            root_run_id="1" * 32,
            parent_run_id=None,
            delegation_depth=0,
            agent_id="test-agent",
            agent_role="orchestrator",
            budget_limits=ExecutionBudget(max_steps=5),
        )

        with execution_context_scope(ctx):
            result = executor.execute(tool, {"path": "test.txt"})

        # Tool executed
        self.assertTrue(tool.execute_called)
        self.assertFalse(result.is_error)

        confirm_events = [e for e in self.observer.events if isinstance(e, ConfirmationResolvedEvent)]
        self.assertEqual(len(confirm_events), 1)
        self.assertTrue(confirm_events[0].approved)

        # Confirm token was consumed
        self.assertEqual(len(manager._confirmations), 1)
        rec = list(manager._confirmations.values())[0]
        self.assertTrue(rec.consumed)

    def test_executor_confirmation_binding_tampering_denied(self) -> None:
        rule = PolicyRule(name="confirm_test", decision=PermissionDecision.REQUIRE_CONFIRMATION, tool_pattern="test_tool")
        manager = PermissionManager(
            policy_engine=PolicyEngine(rules=[rule], default_stance=PermissionDecision.DENY),
            confirmation_handler=DeterministicConfirmationHandler(always_allow=True),
            event_bus=self.bus,
        )
        executor = ToolExecutor(event_bus=self.bus, permission_manager=manager)
        tool = DummyTool(name="test_tool")

        # Tamper with the manager's authorize() to return a record bound to a different run_id
        orig_authorize = manager.authorize

        def tampered_authorize(req: PermissionRequest) -> AuthorizationResult:
            res = orig_authorize(req)
            assert res.confirmation_record is not None
            # Mutate record's bound run_id to simulate cross-run forgery
            res.confirmation_record.run_id = "attacker-run-id"
            return res

        manager.authorize = tampered_authorize  # type: ignore[assignment]

        ctx = ExecutionContext(
            trace_id="0" * 32,
            run_id="1" * 32,
            root_run_id="1" * 32,
            parent_run_id=None,
            delegation_depth=0,
            agent_id="test-agent",
            agent_role="orchestrator",
            budget_limits=ExecutionBudget(max_steps=5),
        )

        with execution_context_scope(ctx):
            result = executor.execute(tool, {"path": "test.txt"})

        # Tool execution must be blocked
        self.assertFalse(tool.execute_called)
        self.assertTrue(result.is_error)
        self.assertEqual(result.error_code, ErrorCode.PERMISSION_DENIED)
        self.assertIn("Confirmation token binding verification failed", result.content)


class TestPermissionsObservability(unittest.TestCase):
    """Unit tests verifying Prometheus, OpenTelemetry, and structured JSON logs for permissions."""

    def test_prometheus_observer_permission_metrics(self) -> None:
        registry = CollectorRegistry()
        observer = PrometheusObserver(registry=registry)

        # Emit evaluated event
        observer.on_event(
            PermissionEvaluatedEvent(
                timestamp=100.0,
                trace_id="0" * 32,
                run_id="1" * 32,
                root_run_id="1" * 32,
                parent_run_id=None,
                agent_id="a1",
                agent_role="orchestrator",
                call_id="c1",
                canonical_tool_identity="builtin:filesystem:read_file",
                tool_name="read_file",
                tool_source=ToolSource.BUILTIN,
                decision=PermissionDecision.ALLOW,
                matched_rule="allow_reads",
                risk_level=RiskLevel.READ_ONLY,
                arguments_fingerprint="fp1",
            )
        )

        # Emit confirmation event
        observer.on_event(
            ConfirmationResolvedEvent(
                timestamp=101.0,
                trace_id="0" * 32,
                run_id="1" * 32,
                root_run_id="1" * 32,
                parent_run_id=None,
                agent_id="a1",
                agent_role="orchestrator",
                call_id="c2",
                confirmation_id="conf-1",
                canonical_tool_identity="builtin:filesystem:modify_file",
                approved=True,
                duration_seconds=0.045,
            )
        )

        # Verify metric counters
        val_eval = registry.get_sample_value(
            "harness_permission_decisions_total",
            {"decision": "allow", "tool_source": "builtin", "risk_level": "read_only"},
        )
        self.assertEqual(val_eval, 1.0)

        val_conf = registry.get_sample_value(
            "harness_permission_confirmations_total",
            {"status": "approved"},
        )
        self.assertEqual(val_conf, 1.0)

    def test_structured_log_observer_permission_events(self) -> None:
        stream = io.StringIO()
        observer = StructuredLogObserver(destination=stream)

        eval_event = PermissionEvaluatedEvent(
            timestamp=100.0,
            trace_id="0" * 32,
            run_id="1" * 32,
            root_run_id="1" * 32,
            parent_run_id=None,
            agent_id="a1",
            agent_role="orchestrator",
            call_id="c1",
            canonical_tool_identity="builtin:filesystem:modify_file",
            tool_name="modify_file",
            tool_source=ToolSource.BUILTIN,
            decision=PermissionDecision.REQUIRE_CONFIRMATION,
            matched_rule="confirm_mutations",
            risk_level=RiskLevel.MUTATING,
            arguments_fingerprint="fp1",
        )
        observer.on_event(eval_event)

        conf_event = ConfirmationResolvedEvent(
            timestamp=101.0,
            trace_id="0" * 32,
            run_id="1" * 32,
            root_run_id="1" * 32,
            parent_run_id=None,
            agent_id="a1",
            agent_role="orchestrator",
            call_id="c1",
            confirmation_id="conf-123",
            canonical_tool_identity="builtin:filesystem:modify_file",
            approved=True,
            duration_seconds=0.12,
        )
        observer.on_event(conf_event)

        output = stream.getvalue()
        self.assertIn('"event":"permission.evaluated"', output)
        self.assertIn('"decision":"require_confirmation"', output)
        self.assertIn('"event":"permission.confirmation_resolved"', output)
        self.assertIn('"approved":true', output)
        self.assertIn('"confirmation_id":"conf-123"', output)


class TestConfigPermissionsParsing(unittest.TestCase):
    """Unit tests for PermissionsConfig loading from YAML."""

    def test_load_config_with_permissions(self) -> None:
        yaml_content = """
agent:
  max_steps: 10
llm:
  base_url: "https://example.com"
  model: "test-model"
  temperature: 0.0
tools:
  workspace_root: "./workspace"
permissions:
  default_stance: "deny"
  confirmation_handler: "deterministic"
  rules:
    - name: "custom_read_allow"
      decision: "allow"
      priority: 150
      tool_pattern: "read_*"
      risk_level: "read_only"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            temp_path = f.name

        try:
            cfg = load_config(temp_path)
            self.assertEqual(cfg.permissions.default_stance, "deny")
            self.assertEqual(cfg.permissions.confirmation_handler, "deterministic")
            self.assertEqual(len(cfg.permissions.rules), 1)
            r = cfg.permissions.rules[0]
            self.assertEqual(r.name, "custom_read_allow")
            self.assertEqual(r.decision, "allow")
            self.assertEqual(r.priority, 150)
            self.assertEqual(r.tool_pattern, "read_*")
        finally:
            Path(temp_path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()

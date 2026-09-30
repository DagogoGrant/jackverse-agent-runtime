"""Unit tests for context-aware, risk-aware permission evaluation (Feature A)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import tempfile
import unittest

from harness.agent.delegation import get_standard_specialist_specs
from harness.config import load_config
from harness.permissions.base import (
    PermissionDecision,
    PermissionRequest,
    RiskLevel,
    canonical_tool_identity,
    compute_arguments_fingerprint,
    summarize_arguments,
)
from harness.permissions.confirmation import DeterministicConfirmationHandler
from harness.permissions.manager import PermissionManager
from harness.permissions.policy import PolicyEngine, PolicyRule
from harness.permissions.risk import RiskClassifier
from harness.tools.base import ToolSource
from harness.tools.workspace import Workspace, WorkspaceBoundaryError


class TestContextualPermissions(unittest.TestCase):
    """Verify deterministic risk classification and context-aware policy evaluation."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.workspace_path = Path(self.tmp_dir.name)
        self.workspace = Workspace(self.workspace_path)

        # Create dummy workspace files
        (self.workspace_path / "docs").mkdir(parents=True, exist_ok=True)
        (self.workspace_path / "docs" / "guide.md").write_text("# Guide", encoding="utf-8")
        (self.workspace_path / ".env").write_text("SECRET=12345", encoding="utf-8")
        (self.workspace_path / "id_rsa").write_text("dummy-key", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _make_request(
        self,
        tool_name: str = "read_file",
        path: str = "docs/guide.md",
        tool_source: ToolSource = ToolSource.BUILTIN,
        server_name: str | None = None,
        agent_role: str = "orchestrator",
        risk_level: RiskLevel | None = None,
    ) -> PermissionRequest:
        arguments = {"path": path} if path else {}
        canon_id = canonical_tool_identity(tool_source, server_name, tool_name)
        resolved_path = str(self.workspace_path / path) if path else None

        return PermissionRequest(
            run_id="run-1",
            agent_id="agent-1",
            agent_role=agent_role,
            delegation_depth=0,
            call_id="call-1",
            canonical_tool_identity=canon_id,
            tool_name=tool_name,
            tool_source=tool_source,
            server_name=server_name,
            arguments=arguments,
            arguments_fingerprint=compute_arguments_fingerprint(arguments),
            arguments_summary=summarize_arguments(tool_name, arguments),
            resource_descriptor=resolved_path,
            risk_level=risk_level,
        )

    def test_sensitive_policy_precedence_over_generic_read(self) -> None:
        """Explicitly prove that a specific sensitive rule takes precedence over a broad read rule.

        read_file("docs/guide.md") -> ALLOW
        read_file(".env") -> REQUIRE_CONFIRMATION
        """
        rules = [
            # Specific high-priority sensitive protection rule
            PolicyRule(
                name="protect_sensitive_env",
                decision=PermissionDecision.REQUIRE_CONFIRMATION,
                priority=200,
                tool_pattern="read_file",
                resource_pattern="*.env*",
                risk_level=RiskLevel.SENSITIVE,
            ),
            # Broad default filesystem read rule
            PolicyRule(
                name="allow_filesystem_reads",
                decision=PermissionDecision.ALLOW,
                priority=100,
                tool_pattern="read_file",
                source=ToolSource.BUILTIN,
                risk_level=RiskLevel.READ_ONLY,
            ),
        ]
        engine = PolicyEngine(rules=rules, default_stance=PermissionDecision.DENY)
        manager = PermissionManager(
            policy_engine=engine,
            confirmation_handler=DeterministicConfirmationHandler(always_allow=False),
            workspace=self.workspace,
        )

        # 1. Reading regular file -> ALLOW
        req_normal = self._make_request(path="docs/guide.md")
        res_normal = manager.authorize(req_normal)
        self.assertEqual(res_normal.decision, PermissionDecision.ALLOW)
        self.assertEqual(res_normal.matched_rule, "allow_filesystem_reads")
        self.assertEqual(res_normal.risk_level, RiskLevel.READ_ONLY)

        # 2. Reading sensitive .env file -> REQUIRE_CONFIRMATION (denied by handler)
        req_sensitive = self._make_request(path=".env")
        res_sensitive = manager.authorize(req_sensitive)
        self.assertEqual(res_sensitive.decision, PermissionDecision.DENY)
        self.assertEqual(res_sensitive.matched_rule, "protect_sensitive_env")
        self.assertEqual(res_sensitive.risk_level, RiskLevel.SENSITIVE)

    def test_sensitive_deny_precedence_even_at_equal_priority(self) -> None:
        """Prove that tie-break (DENY > REQUIRE_CONFIRMATION > ALLOW) prioritizes protection even at equal priority."""
        rules = [
            PolicyRule(
                name="allow_read",
                decision=PermissionDecision.ALLOW,
                priority=100,
                tool_pattern="read_file",
                source=ToolSource.BUILTIN,
            ),
            PolicyRule(
                name="deny_sensitive",
                decision=PermissionDecision.DENY,
                priority=100,
                tool_pattern="read_file",
                resource_pattern="*.env*",
            ),
        ]
        engine = PolicyEngine(rules=rules, default_stance=PermissionDecision.DENY)
        manager = PermissionManager(
            policy_engine=engine,
            confirmation_handler=DeterministicConfirmationHandler(always_allow=True),
        )

        req_env = self._make_request(path=".env")
        res = manager.authorize(req_env)
        self.assertEqual(res.decision, PermissionDecision.DENY)
        self.assertEqual(res.matched_rule, "deny_sensitive")

    def test_permission_request_immutability(self) -> None:
        """Ensure PermissionRequest cannot be modified in-place and manager enriches via replace."""
        req = self._make_request(path="docs/guide.md", risk_level=None)
        self.assertIsNone(req.risk_level)

        # Confirm dataclass is frozen
        with self.assertRaises(FrozenInstanceError):
            req.risk_level = RiskLevel.MUTATING  # type: ignore

        # Authorize and confirm original request object was not modified
        engine = PolicyEngine(
            rules=[PolicyRule(name="allow_all", decision=PermissionDecision.ALLOW, tool_pattern="*")]
        )
        manager = PermissionManager(
            policy_engine=engine,
            confirmation_handler=DeterministicConfirmationHandler(always_allow=True),
        )
        res = manager.authorize(req)

        self.assertEqual(res.decision, PermissionDecision.ALLOW)
        # Original request object still has risk_level is None
        self.assertIsNone(req.risk_level)

    def test_risk_classifier_deterministic_categorization(self) -> None:
        """Verify RiskClassifier mappings across read, mutate, sensitive, critical, network."""
        # Read-only regular file
        req_read = self._make_request(tool_name="read_file", path="docs/guide.md")
        self.assertEqual(RiskClassifier.classify(req_read), RiskLevel.READ_ONLY)

        # Mutating regular file
        req_write = self._make_request(tool_name="create_file", path="src/main.py")
        self.assertEqual(RiskClassifier.classify(req_write), RiskLevel.MUTATING)

        # Sensitive resources (.env, .pem, id_rsa, key)
        for sensitive_path in (".env", ".env.local", "certs/server.pem", "id_rsa", "secrets/api.key"):
            req_s = self._make_request(tool_name="read_file", path=sensitive_path)
            self.assertEqual(
                RiskClassifier.classify(req_s),
                RiskLevel.SENSITIVE,
                f"Failed to classify {sensitive_path} as SENSITIVE",
            )

        # Critical resources (/etc/shadow, /etc/passwd)
        for crit_path in ("/etc/passwd", "/etc/shadow", "etc/sudoers"):
            req_c = self._make_request(tool_name="read_file", path=crit_path)
            self.assertEqual(
                RiskClassifier.classify(req_c),
                RiskLevel.CRITICAL,
                f"Failed to classify {crit_path} as CRITICAL",
            )

        # Network / MCP tools
        req_mcp = self._make_request(
            tool_name="find_connection",
            path="",
            tool_source=ToolSource.MCP,
            server_name="transport_service",
        )
        self.assertEqual(RiskClassifier.classify(req_mcp), RiskLevel.NETWORK)

        # Delegation
        req_del = self._make_request(tool_name="delegate_task", path="")
        self.assertEqual(RiskClassifier.classify(req_del), RiskLevel.READ_ONLY)

    def test_argument_matches_constraint(self) -> None:
        """Verify policy matching based on specific scalar argument patterns."""
        rules = [
            PolicyRule(
                name="allow_config_only",
                decision=PermissionDecision.ALLOW,
                priority=100,
                tool_pattern="read_file",
                argument_matches={"path": "*config*"},
            ),
        ]
        engine = PolicyEngine(rules=rules, default_stance=PermissionDecision.DENY)

        # Matching argument
        req_cfg = self._make_request(tool_name="read_file", path="config/app.json")
        dec, rule, _ = engine.evaluate(req_cfg)
        self.assertEqual(dec, PermissionDecision.ALLOW)
        self.assertEqual(rule, "allow_config_only")

        # Non-matching argument falls back to default stance DENY
        req_other = self._make_request(tool_name="read_file", path="src/main.py")
        dec, rule, _ = engine.evaluate(req_other)
        self.assertEqual(dec, PermissionDecision.DENY)
        self.assertIsNone(rule)

    def test_match_risk_level_constraint(self) -> None:
        """Verify rules can constrain matches to specific RiskLevel."""
        rules = [
            PolicyRule(
                name="deny_sensitive_operations",
                decision=PermissionDecision.DENY,
                priority=200,
                tool_pattern="*",
                match_risk_level=RiskLevel.SENSITIVE,
            ),
            PolicyRule(
                name="allow_normal_reads",
                decision=PermissionDecision.ALLOW,
                priority=100,
                tool_pattern="read_file",
            ),
        ]
        engine = PolicyEngine(rules=rules, default_stance=PermissionDecision.DENY)
        manager = PermissionManager(
            policy_engine=engine,
            confirmation_handler=DeterministicConfirmationHandler(always_allow=True),
        )

        # Normal file read -> matches allow_normal_reads
        req_normal = self._make_request(path="docs/guide.md")
        res_normal = manager.authorize(req_normal)
        self.assertEqual(res_normal.decision, PermissionDecision.ALLOW)
        self.assertEqual(res_normal.matched_rule, "allow_normal_reads")

        # Sensitive file read -> matches deny_sensitive_operations
        req_sens = self._make_request(path=".env")
        res_sens = manager.authorize(req_sens)
        self.assertEqual(res_sens.decision, PermissionDecision.DENY)
        self.assertEqual(res_sens.matched_rule, "deny_sensitive_operations")

    def test_workspace_analyst_registry_least_privilege(self) -> None:
        """Verify capability boundary: workspace_analyst registry does not contain mutating tools."""
        specs = get_standard_specialist_specs()
        analyst_spec = specs["workspace_analyst"]
        self.assertEqual(analyst_spec.role, "workspace_analyst")

        # Capability restriction at the registry level
        self.assertIn("builtin:filesystem:read_file", analyst_spec.allowed_tool_ids)
        self.assertIn("builtin:filesystem:list_directory", analyst_spec.allowed_tool_ids)
        self.assertIn("builtin:filesystem:search_files", analyst_spec.allowed_tool_ids)
        self.assertNotIn("builtin:filesystem:create_file", analyst_spec.allowed_tool_ids)
        self.assertNotIn("builtin:filesystem:modify_file", analyst_spec.allowed_tool_ids)
        self.assertNotIn("builtin:filesystem:create_directory", analyst_spec.allowed_tool_ids)

    def test_workspace_containment_hard_boundary_preserved(self) -> None:
        """Verify Workspace.resolve() raises WorkspaceBoundaryError on path traversal unconditionally."""
        with self.assertRaises(WorkspaceBoundaryError):
            self.workspace.resolve("../../etc/passwd")

        with self.assertRaises(WorkspaceBoundaryError):
            self.workspace.resolve("/etc/shadow")

    def test_yaml_config_parsing_of_contextual_rules(self) -> None:
        """Verify that YAML configuration correctly parses resource_pattern and match_risk_level."""
        yaml_content = """
        agent:
          max_steps: 5
        llm:
          provider: "mock"
          base_url: "http://mock"
          model: "mock-model"
          temperature: 0.0
        tools:
          workspace_root: "./workspace"
        permissions:
          default_stance: "deny"
          rules:
            - name: "protect_env"
              decision: "require_confirmation"
              priority: 250
              tool_pattern: "read_file"
              resource_pattern: "*.env*"
              risk_level: "sensitive"
              match_risk_level: "sensitive"
              argument_matches:
                path: "*.env*"
        """
        temp_yaml = self.workspace_path / "test_config.yaml"
        temp_yaml.write_text(yaml_content, encoding="utf-8")

        cfg = load_config(temp_yaml)
        self.assertEqual(len(cfg.permissions.rules), 1)
        rule = cfg.permissions.rules[0]
        self.assertEqual(rule.name, "protect_env")
        self.assertEqual(rule.decision, "require_confirmation")
        self.assertEqual(rule.priority, 250)
        self.assertEqual(rule.resource_pattern, "*.env*")
        self.assertEqual(rule.risk_level, "sensitive")
        self.assertEqual(rule.match_risk_level, "sensitive")
        self.assertEqual(rule.argument_matches, {"path": "*.env*"})

        # Build PermissionManager from this config
        mgr = PermissionManager.from_config(
            cfg.permissions,
            confirmation_handler=DeterministicConfirmationHandler(always_allow=False),
        )
        self.assertEqual(len(mgr.policy_engine.rules), 1)
        p_rule = mgr.policy_engine.rules[0]
        self.assertEqual(p_rule.resource_pattern, "*.env*")
        self.assertEqual(p_rule.match_risk_level, RiskLevel.SENSITIVE)

    def test_docker_config_loads_sensitive_policy_and_matches_env(self) -> None:
        """Verify config.docker.yaml loads protect_sensitive_workspace_files and matches .env."""
        cfg = load_config(Path("config/config.docker.yaml"))
        mgr = PermissionManager.from_config(
            cfg.permissions,
            confirmation_handler=DeterministicConfirmationHandler(always_allow=False),
        )
        rule_names = [r.name for r in mgr.policy_engine.rules]
        self.assertIn("protect_sensitive_workspace_files", rule_names)

        # Test read_file(".env") matches protect_sensitive_workspace_files
        req_env = self._make_request(
            tool_name="read_file",
            path=".env",
        )
        res_env = mgr.authorize(req_env)
        self.assertEqual(res_env.decision, PermissionDecision.DENY)  # Handled as False by always_allow=False
        self.assertEqual(res_env.matched_rule, "protect_sensitive_workspace_files")
        self.assertEqual(res_env.risk_level, RiskLevel.SENSITIVE)

    def test_explicit_risk_level_preserved_in_manager_authorize(self) -> None:
        """Verify explicitly provided PermissionRequest.risk_level is preserved and not reclassified."""
        rules = [
            PolicyRule(
                name="allow_read",
                decision=PermissionDecision.ALLOW,
                priority=100,
                tool_pattern="read_file",
            )
        ]
        engine = PolicyEngine(rules=rules)
        mgr = PermissionManager(
            policy_engine=engine,
            confirmation_handler=DeterministicConfirmationHandler(always_allow=True),
        )

        req_explicit = self._make_request(
            tool_name="read_file",
            path="docs/guide.md",
            risk_level=RiskLevel.ADMIN,
        )
        res = mgr.authorize(req_explicit)
        self.assertEqual(res.decision, PermissionDecision.ALLOW)
        self.assertEqual(res.risk_level, RiskLevel.ADMIN)


if __name__ == "__main__":
    unittest.main()

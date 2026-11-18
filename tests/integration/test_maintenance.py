"""Maintenance tests: seeded duplicates found, approval gate, merge applied."""

import pytest

from ecosystem_kb.services.maintenance_service import MaintenanceService
from tests.fixtures.kb_fixtures import marker_event
from tests.integration.test_vertical_slice import PAYLOAD

pytestmark = pytest.mark.embedded


async def _seed_duplicate_identities(control_plane):
    """Plant the fault the Maintainer must find (spec §36: seeded duplicate)."""
    await control_plane.identities.upsert_identity(
        canonical_key="system:graphiti", entity_type="System", canonical_name="Graphiti"
    )
    await control_plane.identities.upsert_identity(
        canonical_key="system:graphiti-core", entity_type="System", canonical_name="Graphiti"
    )
    await control_plane.identities.add_alias("system:graphiti", "Graphiti Core")


class TestMaintainer:
    async def test_audit_finds_seeded_duplicate(self, services, control_plane):
        await _seed_duplicate_identities(control_plane)
        maintenance = MaintenanceService(control_plane, services.graphiti)
        report = await maintenance.run_audit("ecosystem-dev")
        dups = [d for d in report.duplicate_entities if d["canonical_name"] == "Graphiti"]
        assert dups, "seeded duplicate not detected"
        assert any(
            set(d["keys"]) == {"system:graphiti", "system:graphiti-core"} for d in dups
        )

    async def test_approval_gate_blocks_unapproved_apply(self, services, control_plane):
        from contracts.maintenance import MaintenanceOperation

        from ecosystem_kb.core.exceptions import AuthorizationError

        await _seed_duplicate_identities(control_plane)
        maintenance = MaintenanceService(control_plane, services.graphiti)
        report = await maintenance.run_audit("ecosystem-dev")
        merge_proposal = next(
            p
            for p in report.proposals
            if p.operation == MaintenanceOperation.ENTITY_MERGE
        )
        with pytest.raises(AuthorizationError):
            await maintenance.apply_action(merge_proposal.action_id, operator="tester")

    async def test_merge_after_approval(self, services, control_plane):
        from contracts.maintenance import ApprovalStatus, MaintenanceOperation, ProposalDecision

        await _seed_duplicate_identities(control_plane)
        maintenance = MaintenanceService(control_plane, services.graphiti)
        report = await maintenance.run_audit("ecosystem-dev")
        proposal = next(
            p
            for p in report.proposals
            if p.operation == MaintenanceOperation.ENTITY_MERGE
        )
        await maintenance.decide_proposal(
            ProposalDecision(
                action_id=proposal.action_id,
                decision=ApprovalStatus.APPROVED,
                reviewer="test",
                reason="canonical alias match",
            )
        )
        applied = await maintenance.apply_action(proposal.action_id, operator="test")
        assert applied.execution_status == "completed"

        # The merged identity is marked; aliases re-pointed.
        merged = await control_plane.identities.get("system:graphiti-core")
        assert merged.status == "merged"
        aliases = await control_plane.identities.aliases_for("system:graphiti")
        assert any(a.alias_value == "Graphiti Core" for a in aliases)

    async def test_graph_health_in_report(self, services):
        maintenance = MaintenanceService(services.repos, services.graphiti)
        event = marker_event("maint:health:1", PAYLOAD)
        from ecosystem_kb.adapters.graphiti.ingestion import ingest_event

        await ingest_event(services.graphiti, event, "ecosystem-dev")
        report = await maintenance.run_audit("ecosystem-dev")
        assert report.summary["graph_healthy"] is True
        # The health check (query path, no exceptions) is what this test
        # verifies; per-namespace counts are covered by the vertical slice.

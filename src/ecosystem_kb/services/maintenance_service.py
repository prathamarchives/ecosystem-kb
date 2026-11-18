"""Maintenance service: audits, proposals, approval gate, apply (spec §16).

Deterministic first (ADR-009): duplicate detection via the identity registry
and alias table; contradiction surfacing via relation vocabulary; orphans via
name resolution failures. LLM-assisted judgment is a later, bounded addition.
"""

from __future__ import annotations

from datetime import UTC, datetime

from contracts.maintenance import (
    APPROVAL_REQUIRED_OPERATIONS,
    ApprovalStatus,
    MaintenanceAction,
    MaintenanceOperation,
    MaintenanceReport,
    ProposalDecision,
)

from ecosystem_kb.adapters.graphiti.health import check_graph
from ecosystem_kb.adapters.postgres.repositories import Repositories
from ecosystem_kb.core.exceptions import AuthorizationError, NotFoundError
from ecosystem_kb.core.identity import AliasResolver


class MaintenanceService:
    def __init__(self, repos: Repositories, graphiti) -> None:
        self.repos = repos
        self.graphiti = graphiti
        # Proposal cache keyed to THIS control plane (tests use isolated DBs).
        # The review queue is the durable record; this cache is the working set.
        if not hasattr(repos, "_kb_maintenance_actions"):
            repos._kb_maintenance_actions = {}
        self._actions = repos._kb_maintenance_actions

    async def run_audit(self, namespace: str) -> MaintenanceReport:
        """Audit mode: analyze, report, propose. Never mutate."""
        report = MaintenanceReport(namespace=namespace)

        health = await check_graph(self.graphiti, namespace)
        report.summary["graph_healthy"] = health.ok
        report.summary["entities"] = health.entity_count
        report.summary["edges"] = health.edge_count
        report.summary["episodes"] = health.episode_count

        # Duplicate identities: same canonical name registered twice, or an
        # alias pointing at more than one key.
        identities = await self._all_identities()
        by_name: dict[str, list] = {}
        for identity in identities:
            by_name.setdefault(identity.canonical_name, []).append(identity)
        for name, group in by_name.items():
            if len(group) > 1:
                entry = {
                    "canonical_name": name,
                    "keys": [g.canonical_key for g in group],
                }
                report.duplicate_entities.append(entry)
                # Stable action key: same duplicate pair re-audits to the
                # SAME action id, so approve-then-apply works across instances.
                action_id = f"dupaudit:{group[0].canonical_key}:{':'.join(g.canonical_key for g in group[1:])}"
                existing = self._actions.get(action_id)
                if existing is not None and existing.approval_status.value == "pending":
                    action = existing
                else:
                    action = MaintenanceAction(
                        action_id=action_id,
                        operation=MaintenanceOperation.ENTITY_MERGE,
                        target=group[0].canonical_key,
                        candidates=[g.canonical_key for g in group[1:]],
                        evidence=[f"shared canonical name '{name}'"],
                        reason="name collision across canonical keys",
                        risk_level="medium",
                    )
                    self._actions[action.action_id] = action
                report.proposals.append(action)

        # Ambiguous aliases.
        aliases = await self._all_aliases()
        alias_targets: dict[str, set[str]] = {}
        for alias in aliases:
            alias_targets.setdefault(alias.alias_value, set()).add(alias.canonical_key)
        for alias_value, targets in alias_targets.items():
            if len(targets) > 1:
                report.contradictions.append(
                    {"alias": alias_value, "targets": sorted(targets)}
                )
                action = MaintenanceAction(
                    operation=MaintenanceOperation.ALIAS_CANDIDATE_DISCOVERY,
                    target=alias_value,
                    candidates=sorted(targets),
                    evidence=[f"alias maps to {len(targets)} identities"],
                    reason="ambiguous alias",
                    risk_level="low",
                )
                self._actions[action.action_id] = action
                report.proposals.append(action)

        report.summary["duplicates"] = len(report.duplicate_entities)
        report.summary["ambiguous_aliases"] = len(report.contradictions)
        return report

    async def decide_proposal(self, decision: ProposalDecision) -> MaintenanceAction:
        """Reviewer approves or rejects a proposal (approval gate, LAW-012)."""
        action = self._actions.get(decision.action_id)
        if action is None:
            raise NotFoundError(f"unknown maintenance action {decision.action_id}")
        if action.approval_status != ApprovalStatus.PENDING:
            raise ValueError(f"action already {action.approval_status.value}")
        action.approval_status = (
            ApprovalStatus.APPROVED
            if decision.decision == ApprovalStatus.APPROVED
            else ApprovalStatus.REJECTED
        )
        await self.repos.audit.record(
            actor=f"reviewer:{decision.reviewer}",
            action="maintenance_decision",
            resource_type="maintenance_action",
            resource_id=action.action_id,
            reason=decision.reason or action.reason,
        )
        await self.repos.reviews.enqueue(
            item_type="maintenance_decision",
            item_reference=action.action_id,
            proposed_action="record_decision",
            reason=f"{action.approval_status.value}: {decision.reason}",
        )
        return action

    async def apply_action(self, action_id: str, operator: str) -> MaintenanceAction:
        """Apply mode: execute an APPROVED action. Destructive operations
        require approval first; reversible audits apply immediately."""
        action = self._actions.get(action_id)
        if action is None:
            raise NotFoundError(f"unknown maintenance action {action_id}")
        if action.operation in APPROVAL_REQUIRED_OPERATIONS and (
            action.approval_status != ApprovalStatus.APPROVED
        ):
            raise AuthorizationError(
                f"operation {action.operation.value} requires approval before apply"
            )
        if action.approval_status == ApprovalStatus.REJECTED:
            raise AuthorizationError("rejected actions cannot be applied")

        if action.operation in (
            MaintenanceOperation.DUPLICATE_ENTITY_AUDIT,
            MaintenanceOperation.ENTITY_MERGE,
        ) and action.candidates:
            resolver = await self._load_resolver()
            from sqlalchemy import update as _update

            from ecosystem_kb.adapters.postgres.models import KbEntityIdentity

            for candidate in action.candidates:
                moved = resolver.merge(candidate, action.target)
                # Durable status: the candidate identity is merged, not active.
                async with self.repos.cp.session() as s:
                    await s.execute(
                        _update(KbEntityIdentity)
                        .where(KbEntityIdentity.canonical_key == candidate)
                        .values(status="merged")
                    )
                    await s.commit()
                await self.repos.audit.record(
                    actor=f"maintainer:{operator}",
                    action="merge_identity",
                    resource_type="identity",
                    resource_id=candidate,
                    reason=f"merged into {action.target}",
                    before_reference=candidate,
                    after_reference=action.target,
                )
                for alias in moved:
                    await self.repos.identities.add_alias(
                        action.target, alias, alias_source="merge", confidence_basis="exact"
                    )

        action.execution_status = "completed"
        action.executed_at = datetime.now(UTC)
        await self.repos.audit.record(
            actor=f"maintainer:{operator}",
            action="apply_maintenance",
            resource_type="maintenance_action",
            resource_id=action.action_id,
            reason=action.reason,
        )
        return action

    async def _all_identities(self):
        from sqlalchemy import select

        from ecosystem_kb.adapters.postgres.models import KbEntityIdentity

        async with self.repos.cp.session() as s:
            res = await s.scalars(select(KbEntityIdentity))
            return list(res)

    async def _all_aliases(self):
        from sqlalchemy import select

        from ecosystem_kb.adapters.postgres.models import KbAlias

        async with self.repos.cp.session() as s:
            res = await s.scalars(select(KbAlias))
            return list(res)

    async def _load_resolver(self) -> AliasResolver:
        resolver = AliasResolver()
        for identity in await self._all_identities():
            from ecosystem_kb.core.identity import IdentityRecord

            resolver.register(
                IdentityRecord(
                    canonical_key=identity.canonical_key,
                    entity_type=identity.entity_type,
                    canonical_name=identity.canonical_name,
                    status=identity.status,
                )
            )
        for alias in await self._all_aliases():
            resolver.add_alias(alias.canonical_key, alias.alias_value)
        return resolver

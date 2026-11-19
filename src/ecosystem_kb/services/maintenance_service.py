"""Maintenance service (Gate 4, review P1): durable proposals in PostgreSQL.

Everything lives in kb_maintenance_actions and survives restarts and process
boundaries:
  - run_audit analyzes and UPSERTS proposals (stable action_id derived from
    operation + sorted targets; proposal_version = hash of the semantic
    content, so a changed proposal cannot silently hijack an approval).
  - decide_proposal binds the decision to the exact proposal_version.
  - apply_action is restart-safe and idempotent: begin_apply records the
    attempt, each step is a compare-and-set, already-completed actions are
    no-ops, and an ENTITY_MERGE merge is implemented as an explicit
    control-plane identity+alias merge (renamed accurately: this is a
    registry merge, not a graph merge — see ADR-013).
  - Duplicate detection requires SAME entity_type AND same canonical name
    (or shared alias); display-name coincidence across types never proposes
    a merge.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from contracts.maintenance import (
    APPROVAL_REQUIRED_OPERATIONS,
    ApprovalStatus,
    MaintenanceAction,
    MaintenanceOperation,
    MaintenanceReport,
    ProposalDecision,
)
from sqlalchemy import update as sa_update

from ecosystem_kb.adapters.graphiti.health import check_graph
from ecosystem_kb.adapters.postgres.models import KbEntityIdentity
from ecosystem_kb.adapters.postgres.repositories import Repositories
from ecosystem_kb.core.exceptions import AuthorizationError, NotFoundError


def _action_id_for(operation: str, target: str, candidates: list[str]) -> str:
    return f"{operation}:{target}:{':'.join(sorted(candidates))}"


def _proposal_version(operation: str, target: str, candidates: list[str], evidence: list[str]) -> str:
    payload = "|".join([operation, target, *sorted(candidates), *sorted(evidence)])
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


class MaintenanceService:
    def __init__(self, repos: Repositories, graphiti) -> None:
        self.repos = repos
        self.graphiti = graphiti

    async def run_audit(self, namespace: str) -> MaintenanceReport:
        """Audit mode: analyze, report, propose. Never mutates graph or
        identity state. Proposals are persisted (upsert) immediately."""
        report = MaintenanceReport(namespace=namespace)

        health = await check_graph(self.graphiti, namespace)
        report.summary["graph_healthy"] = health.ok
        report.summary["entities"] = health.entity_count
        report.summary["edges"] = health.edge_count
        report.summary["episodes"] = health.episode_count

        identities = await self._all_identities()

        # Duplicate identities: same canonical name AND same entity type
        # (review P1: type-aware; display-name coincidence is not evidence).
        by_signature: dict[tuple[str, str], list] = {}
        for identity in identities:
            if identity.status != "active":
                continue
            by_signature.setdefault(
                (identity.canonical_name, identity.entity_type), []
            ).append(identity)
        for (name, entity_type), group in by_signature.items():
            if len(group) < 2:
                continue
            entry = {
                "canonical_name": name,
                "entity_type": entity_type,
                "keys": [g.canonical_key for g in group],
            }
            report.duplicate_entities.append(entry)
            action = await self._upsert_proposal(
                operation=MaintenanceOperation.ENTITY_MERGE,
                namespace=namespace,
                target=group[0].canonical_key,
                candidates=[g.canonical_key for g in group[1:]],
                evidence=[
                    f"shared canonical name '{name}'",
                    f"shared entity type '{entity_type}'",
                ],
                reason="name+type collision across canonical keys",
                risk_level="medium",
            )
            report.proposals.append(action)

        # Ambiguous aliases (contradiction signal).
        aliases = await self._all_aliases()
        alias_targets: dict[str, set[str]] = {}
        for alias in aliases:
            alias_targets.setdefault(alias.alias_value, set()).add(alias.canonical_key)
        for alias_value, targets in alias_targets.items():
            if len(targets) > 1:
                report.contradictions.append(
                    {"alias": alias_value, "targets": sorted(targets)}
                )
                action = await self._upsert_proposal(
                    operation=MaintenanceOperation.ALIAS_CANDIDATE_DISCOVERY,
                    namespace=namespace,
                    target=alias_value,
                    candidates=sorted(targets),
                    evidence=[f"alias maps to {len(targets)} identities"],
                    reason="ambiguous alias",
                    risk_level="low",
                )
                report.proposals.append(action)

        report.summary["duplicates"] = len(report.duplicate_entities)
        report.summary["ambiguous_aliases"] = len(report.contradictions)
        return report

    async def _upsert_proposal(
        self,
        operation: MaintenanceOperation,
        namespace: str,
        target: str,
        candidates: list[str],
        evidence: list[str],
        reason: str,
        risk_level: str = "medium",
    ) -> MaintenanceAction:
        action_id = _action_id_for(operation.value, target, candidates)
        proposal_version = _proposal_version(
            operation.value, target, candidates, evidence
        )
        row = await self.repos.maintenance.upsert_action(
            {
                "action_id": action_id,
                "operation": operation.value,
                "namespace": namespace,
                "target": target,
                "candidates": candidates,
                "evidence": evidence,
                "reason": reason,
                "risk_level": risk_level,
                "reversibility": "reversible",
                "proposal_version": proposal_version,
            }
        )
        return self._action_from_row(row)

    @staticmethod
    def _action_from_row(row) -> MaintenanceAction:
        return MaintenanceAction(
            action_id=row.action_id,
            operation=MaintenanceOperation(row.operation),
            target=row.target,
            candidates=list(row.candidates or []),
            evidence=list(row.evidence or []),
            reason=row.reason,
            risk_level=row.risk_level,
            reversibility=row.reversibility,
            approval_status=ApprovalStatus(row.approval_status),
            execution_status=row.execution_status,
            created_at=row.created_at,
            executed_at=row.executed_at,
        )

    async def get_action(self, action_id: str) -> MaintenanceAction:
        row = await self.repos.maintenance.get(action_id)
        if row is None:
            raise NotFoundError(f"unknown maintenance action {action_id}")
        return self._action_from_row(row)

    async def decide_proposal(self, decision: ProposalDecision) -> MaintenanceAction:
        """Reviewer approves/rejects, bound to the exact proposal version."""
        row = await self.repos.maintenance.get(decision.action_id)
        if row is None:
            raise NotFoundError(f"unknown maintenance action {decision.action_id}")
        updated = await self.repos.maintenance.decide(
            decision.action_id,
            decision.decision.value,
            decision.reviewer,
            decision.reason,
        )
        await self.repos.audit.record(
            actor=f"reviewer:{decision.reviewer}",
            action="maintenance_decision",
            resource_type="maintenance_action",
            resource_id=decision.action_id,
            reason=f"v{row.proposal_version[:8]} {decision.decision.value}: {decision.reason}",
        )
        return self._action_from_row(updated)

    async def apply_action(self, action_id: str, operator: str) -> MaintenanceAction:
        """Idempotent, restart-safe application of an APPROVED action.

        - approved-only for approval-required operations (LAW-012)
        - already-completed -> no-op returning current state
        - begin_apply records the attempt; each merge step is a guarded update
        - a crash mid-apply leaves 'running'; re-apply resumes cleanly because
          every step is a compare-and-set against current state
        """
        row = await self.repos.maintenance.get(action_id)
        if row is None:
            raise NotFoundError(f"unknown maintenance action {action_id}")
        if row.execution_status == "completed":
            return self._action_from_row(row)  # idempotent no-op
        if row.operation in {op.value for op in APPROVAL_REQUIRED_OPERATIONS} and (
            row.approval_status != ApprovalStatus.APPROVED.value
        ):
            raise AuthorizationError(
                f"operation {row.operation} requires approval before apply "
                f"(current: {row.approval_status})"
            )

        await self.repos.maintenance.begin_apply(action_id, operator)

        try:
            if row.operation == MaintenanceOperation.ENTITY_MERGE.value and row.candidates:
                await self._apply_identity_merge(row)
            # ALIAS_CANDIDATE_DISCOVERY is audit-only: no mutation on apply.
            finished = await self.repos.maintenance.finish_apply(action_id, "completed")
        except Exception:
            await self.repos.maintenance.finish_apply(action_id, "failed")
            raise

        await self.repos.audit.record(
            actor=f"maintainer:{operator}",
            action="apply_maintenance",
            resource_type="maintenance_action",
            resource_id=action_id,
            reason=row.reason,
        )
        return self._action_from_row(finished)

    async def _apply_identity_merge(self, row) -> None:
        """Registry merge: candidate identities fold into the target. Each
        candidate's aliases re-point to the target and its status becomes
        'merged'. Every step is a guarded UPDATE, so a crash mid-merge
        resumes correctly on re-apply."""
        from sqlalchemy import select

        target = row.target
        for candidate in row.candidates:
            # Guard: only active candidates merge (already-merged is a no-op).
            async with self.repos.cp.session() as s:
                cand = (
                    await s.execute(
                        select(KbEntityIdentity).where(
                            KbEntityIdentity.canonical_key == candidate
                        )
                    )
                ).scalar_one_or_none()
                if cand is None:
                    continue
                if cand.status == "merged":
                    continue  # idempotent: already folded

            # Re-point aliases (insert target alias where missing).
            aliases = await self.repos.identities.aliases_for(candidate)
            for alias in aliases:
                await self.repos.identities.add_alias(
                    target, alias.alias_value, alias_source="merge", confidence_basis="exact"
                )
            # Mark merged (guarded).
            async with self.repos.cp.session() as s:
                await s.execute(
                    sa_update(KbEntityIdentity)
                    .where(
                        KbEntityIdentity.canonical_key == candidate,
                        KbEntityIdentity.status == "active",
                    )
                    .values(status="merged", updated_at=datetime.now(UTC))
                )
                await s.commit()
            await self.repos.audit.record(
                actor="maintainer",
                action="merge_identity",
                resource_type="identity",
                resource_id=candidate,
                reason=f"registry merge into {target}",
                before_reference=candidate,
                after_reference=target,
            )

    async def _all_identities(self):
        from sqlalchemy import select

        from ecosystem_kb.adapters.postgres.models import KbEntityIdentity as K

        async with self.repos.cp.session() as s:
            res = await s.scalars(select(K))
            return list(res)

    async def _all_aliases(self):
        from sqlalchemy import select

        from ecosystem_kb.adapters.postgres.models import KbAlias

        async with self.repos.cp.session() as s:
            res = await s.scalars(select(KbAlias))
            return list(res)

    # Back-compat for old callers
    async def decide(self, decision: ProposalDecision) -> MaintenanceAction:
        return await self.decide_proposal(decision)

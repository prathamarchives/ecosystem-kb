"""Graph health checks (readiness + maintainer input)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC

from graphiti_core.graphiti import Graphiti


@dataclass
class GraphHealth:
    ok: bool
    namespace: str
    entity_count: int = 0
    edge_count: int = 0
    episode_count: int = 0
    detail: str = ""


async def check_graph(graphiti: Graphiti, namespace: str) -> GraphHealth:
    """Count core records in the namespace via driver queries."""
    from datetime import datetime

    try:
        episodes = await graphiti.retrieve_episodes(
            reference_time=datetime.now(UTC), group_ids=[namespace], last_n=1000
        )
        episode_count = len(episodes or [])
    except Exception as exc:  # graph unreachable -> not ready
        return GraphHealth(ok=False, namespace=namespace, detail=f"episode query failed: {exc}")

    driver = graphiti.driver
    entity_count = 0
    edge_count = 0
    try:
        # add_episode with group_id != driver._database runs on a cloned driver
        # bound to the namespace graph (graphiti _resolve_request_scope). Query
        # that same graph name for counts.
        ns_driver = driver.with_database(namespace) if namespace != getattr(driver, "_database", None) else driver
        entity_rows = await ns_driver.execute_query(
            "MATCH (n:Entity) WHERE n.group_id = $ns RETURN count(n) AS c", ns=namespace
        )
        for row in entity_rows or []:
            entity_count = int(row.get("c", 0)) if isinstance(row, dict) else 0
        edge_rows = await ns_driver.execute_query(
            "MATCH ()-[e:RELATES_TO]->() WHERE e.group_id = $ns RETURN count(e) AS c",
            ns=namespace,
        )
        for row in edge_rows or []:
            edge_count = int(row.get("c", 0)) if isinstance(row, dict) else 0
    except Exception as exc:
        return GraphHealth(ok=False, namespace=namespace, detail=f"count query failed: {exc}")

    return GraphHealth(
        ok=True,
        namespace=namespace,
        entity_count=entity_count,
        edge_count=edge_count,
        episode_count=episode_count,
    )

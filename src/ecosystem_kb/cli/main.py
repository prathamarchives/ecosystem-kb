"""CLI: bootstrap, client registration, worker run, graph health."""

from __future__ import annotations

import argparse
import asyncio

from ecosystem_kb.adapters.graphiti.client import build_graphiti
from ecosystem_kb.adapters.graphiti.health import check_graph
from ecosystem_kb.adapters.postgres.repositories import ControlPlane, Repositories
from ecosystem_kb.core.config import KbSettings, get_settings


async def _bootstrap(settings: KbSettings, dev_token: str | None) -> None:
    cp = ControlPlane(settings.database_url)
    await cp.create_all()
    repos = Repositories(cp)
    token = dev_token or "local-dev-token-1"
    existing = await repos.clients.find_by_token(token)
    if existing is None:
        await repos.clients.register(
            "local-dev", "Local development client", ["kb:read", "kb:write", "kb:review", "kb:maintain", "kb:admin"], settings.namespace_list
        )
        # register() generates its own token; for the fixed dev token we insert directly.
    print("control plane ready:", settings.database_url)
    print("namespaces:", settings.namespace_list)
    await cp.dispose()


async def _register_client(args) -> None:
    from contracts.clients import ROLE_SCOPES

    settings = get_settings()
    cp = ControlPlane(settings.database_url)
    await cp.create_all()
    repos = Repositories(cp)
    scopes = ROLE_SCOPES.get(args.role, ["kb:read"])
    token = await repos.clients.register(
        args.client_id, args.name or args.client_id, scopes, args.namespaces.split(",")
    )
    print(f"client '{args.client_id}' registered with scopes {scopes}")
    print(f"bearer token (store safely, shown once): {token}")
    await cp.dispose()


async def _run_worker() -> None:
    settings = get_settings()
    cp = ControlPlane(settings.database_url)
    await cp.create_all()
    graphiti = build_graphiti(settings)
    await graphiti.build_indices_and_constraints()

    from ecosystem_kb.services.event_service import KbServices
    from ecosystem_kb.services.maintenance_service import MaintenanceService
    from ecosystem_kb.workers.ingestion import IngestionWorker

    services = KbServices(Repositories(cp), graphiti, settings)
    _maintenance = MaintenanceService(Repositories(cp), graphiti)
    worker = IngestionWorker(services, settings)
    print(f"ingestion worker {worker.worker_id} running (ctrl-c to stop)")
    try:
        await worker.run()
    finally:
        await cp.dispose()
        await graphiti.close()


async def _graph_health(namespace: str) -> None:
    settings = get_settings()
    graphiti = build_graphiti(settings)
    health = await check_graph(graphiti, namespace)
    print(
        f"healthy={health.ok} namespace={health.namespace} "
        f"entities={health.entity_count} edges={health.edge_count} "
        f"episodes={health.episode_count} detail={health.detail}"
    )
    await graphiti.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="kb", description="ecosystem-kb CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("bootstrap", help="Create control-plane tables")
    sub.add_parser("worker", help="Run the ingestion worker")

    p_client = sub.add_parser("register-client", help="Register an integrating client")
    p_client.add_argument("client_id")
    p_client.add_argument("--name", default=None)
    p_client.add_argument("--role", default="reader", choices=["reader", "contributor", "reviewer", "maintainer", "administrator"])
    p_client.add_argument("--namespaces", default="ecosystem-dev")

    p_health = sub.add_parser("graph-health", help="Check graph store health")
    p_health.add_argument("--namespace", default=None)

    args = parser.parse_args(argv)
    if args.command == "bootstrap":
        asyncio.run(_bootstrap(get_settings(), None))
    elif args.command == "register-client":
        asyncio.run(_register_client(args))
    elif args.command == "worker":
        asyncio.run(_run_worker())
    elif args.command == "graph-health":
        ns = args.namespace or get_settings().default_namespace
        asyncio.run(_graph_health(ns))


if __name__ == "__main__":
    main()

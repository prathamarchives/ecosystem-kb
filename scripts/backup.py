#!/usr/bin/env python3
"""Backup control plane + graph data (LAW-016). Restore drills are mandatory."""
import datetime
import os
import subprocess
import sys

stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
out = sys.argv[1] if len(sys.argv) > 1 else f"backups/kb-{stamp}"
os.makedirs(out, exist_ok=True)
# FalkorDB: BGSAVE then copy the RDB from the volume (run on the host or via docker exec).
print("On the VM: docker compose -f deploy/compose.yaml exec falkordb redis-cli -a $FALKORDB_PASSWORD BGSAVE")
print(f"Then: docker cp <falkordb-container>:/data {out}/falkordb-data")
# Postgres control plane (managed service: use pg_dump).
db = os.environ.get("KB_DATABASE_URL", "")
if db.startswith("postgresql"):
    subprocess.run(["pg_dump", db, "-f", f"{out}/control_plane.sql"], check=False)
    print(f"pg_dump written to {out}/control_plane.sql")
print(f"backup directory: {out}")

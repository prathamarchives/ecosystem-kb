import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from tests.fixtures.kb_fixtures import (  # noqa: E402,F401
    admin_client,
    control_plane,
    embedded_graph,
    marker_event,
    reader_client,
    services,
)

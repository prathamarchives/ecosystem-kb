"""Ecosystem KB controlled ontology (LAW-006). Versioned; see versions.py."""
from ontology.registry import REGISTRY, OntologyRegistry, OntologyValidationError, QuarantinedEdge
from ontology.versions import ONTOLOGY_VERSION, OntologyVersion

__all__ = [
    "OntologyRegistry",
    "OntologyValidationError",
    "QuarantinedEdge",
    "REGISTRY",
    "ONTOLOGY_VERSION",
    "OntologyVersion",
]

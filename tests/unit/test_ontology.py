"""Ontology unit tests: registry gate, quarantine, versions, variables."""

import pytest
from ontology.enums import VariableKind, VariableScope
from ontology.graph_edges import GRAPH_EDGE_TYPES
from ontology.graph_entities import GRAPH_ENTITY_TYPES, ProjectEntity, SystemEntity
from ontology.registry import REGISTRY, OntologyValidationError
from ontology.relationships import allowed_relations, validate_edge
from ontology.variables import VariableDefinitionSpec, variable_canonical_key
from ontology.versions import ONTOLOGY_VERSION, assert_compatible


class TestRegistry:
    def test_describe_counts(self):
        d = REGISTRY.describe()
        assert d["entity_types"] == 14
        assert d["relationships"] == 16
        assert d["version"] == "0.1.0"

    def test_valid_edge_passes(self):
        assert validate_edge("DEPENDS_ON", "System", "System")
        assert validate_edge("SUPERSEDES", "Decision", "Decision")
        assert validate_edge("TESTS", "Experiment", "Claim")

    def test_domain_range_violation_rejected(self):
        with pytest.raises(OntologyValidationError):
            REGISTRY.validate_relationship("DEPENDS_ON", "Claim", "Source")

    def test_unknown_relationship_rejected(self):
        with pytest.raises(OntologyValidationError, match="unknown relationship"):
            REGISTRY.validate_relationship("IS_FRIENDS_WITH", "System", "System")

    def test_quarantine_records_reason(self):
        q = REGISTRY.quarantine_edge("HATES", "System", "Claim", fact="x")
        assert "unknown relationship" in q.reason

    def test_key_prefix_lookup(self):
        assert REGISTRY.type_for_canonical_key("system:arivoo") == "System"
        assert REGISTRY.type_for_canonical_key("claim:c-1") == "Claim"
        assert REGISTRY.type_for_canonical_key("nope:x") is None

    def test_edge_map_types_exist_in_edge_models(self):
        edge_map = REGISTRY.graphiti_edge_type_map()
        for (_, _), relations in edge_map.items():
            for rel in relations:
                assert rel in GRAPH_EDGE_TYPES, f"{rel} missing Pydantic edge model"

    def test_allowed_relations(self):
        rels = allowed_relations("System", "System")
        assert "DEPENDS_ON" in rels
        assert "SUPERSEDES" not in rels


class TestGraphProjections:
    def test_entity_projections_are_collision_free(self):
        from graphiti_core.utils.ontology_utils.entity_types_utils import validate_entity_types

        assert validate_entity_types(GRAPH_ENTITY_TYPES)

    def test_entity_type_ids_are_one_based(self):
        # 0 is the generic Entity; our ordered types start at 1.
        names = list(GRAPH_ENTITY_TYPES)
        assert names[0] == "Project"
        assert names[1] == "System"

    def test_projection_defaults_validate(self):
        SystemEntity()
        ProjectEntity()

    def test_all_fourteen_types_present(self):
        assert len(GRAPH_ENTITY_TYPES) == 14
        assert len(GRAPH_EDGE_TYPES) == 16


class TestVersions:
    def test_exact_version_compatible(self):
        assert_compatible(ONTOLOGY_VERSION.version)

    def test_minor_backward_compatible(self):
        assert_compatible("0.0.1")

    def test_major_mismatch_rejected(self):
        with pytest.raises(ValueError, match="major-version mismatch"):
            assert_compatible("1.0.0")

    def test_newer_minor_rejected(self):
        with pytest.raises(ValueError, match="newer"):
            assert_compatible("0.2.0")


class TestVariables:
    def test_canonical_key_builds(self):
        key = variable_canonical_key(VariableScope.SYSTEM, "ecosystem-kb", "api_timeout")
        assert key == "variable:system/ecosystem-kb/api_timeout"

    def test_same_name_different_scope_differs(self):
        a = variable_canonical_key(VariableScope.SYSTEM, "kb", "latency")
        b = variable_canonical_key(VariableScope.MODEL, "kb", "latency")
        assert a != b

    def test_spec_validates_type_of_default(self):
        with pytest.raises(Exception):
            VariableDefinitionSpec(
                name="api_timeout",
                kind=VariableKind.CONFIGURATION,
                data_type="integer",
                default_value="not-an-int",
            )

    def test_spec_derives_key(self):
        spec = VariableDefinitionSpec(
            name="elasticity",
            kind=VariableKind.MODEL_PARAMETER,
            data_type="number",
            scope=VariableScope.MODEL,
            owner_reference="model:demand",
        )
        assert spec.derived_canonical_key().startswith("variable:model/model:demand/")

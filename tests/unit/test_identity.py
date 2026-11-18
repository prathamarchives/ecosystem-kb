"""Identity tests: canonical keys, alias resolution, merges (LAW-005)."""

from ecosystem_kb.core.identity import (
    AliasResolver,
    IdentityRecord,
    canonical_key,
    slugify,
)


class TestCanonicalKeys:
    def test_slugify_folds_unicode(self):
        assert slugify("Café System V2") == "cafe-system-v2"

    def test_canonical_key_format(self):
        assert canonical_key("system", "Arivoo") == "system:arivoo"

    def test_canonical_key_rejects_garbage_prefix(self):
        import pytest

        with pytest.raises(ValueError):
            canonical_key("NOT A TYPE", "x")


class TestAliasResolver:
    def _rec(self, key, name):
        return IdentityRecord(
            canonical_key=key, entity_type="System", canonical_name=name
        )

    def test_register_and_resolve_exact(self):
        r = AliasResolver()
        r.register(self._rec("system:arivoo", "Arivoo"))
        assert r.resolve("Arivoo") == "system:arivoo"

    def test_case_and_space_variants(self):
        r = AliasResolver()
        r.register(self._rec("system:arivoo", "Arivoo"))
        assert r.resolve("arivoo") == "system:arivoo"
        assert r.resolve("ARIVOO") == "system:arivoo"

    def test_ambiguous_returns_none(self):
        r = AliasResolver()
        r.register(self._rec("system:arivoo", "Arivoo"))
        r.register(self._rec("project:arivoo", "Arivoo"))
        assert r.resolve("Arivoo") is None
        assert len(r.ambiguous("Arivoo")) == 2

    def test_merge_repoints_aliases(self):
        r = AliasResolver()
        r.register(self._rec("system:graphiti", "Graphiti"))
        r.register(self._rec("system:graphiti-core", "Graphiti Core"))
        r.add_alias("system:graphiti", "Graphiti Core", "llm_proposed", "extraction")
        moved = r.merge("system:graphiti-core", "system:graphiti")
        assert moved  # aliases moved
        assert r.resolve("Graphiti Core") == "system:graphiti"
        rec = r._records["system:graphiti-core"]
        assert rec.status == "merged"

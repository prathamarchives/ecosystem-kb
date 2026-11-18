"""Typed error hierarchy mapped to HTTP statuses by the API layer."""

from __future__ import annotations


class KbError(Exception):
    status_code = 500
    error_code = "internal_error"


class ValidationError(KbError):
    status_code = 422
    error_code = "validation_error"


class ProvenanceError(ValidationError):
    error_code = "provenance_missing"


class SecretContentError(KbError):
    status_code = 422
    error_code = "secret_content_rejected"


class DuplicateEventError(KbError):
    status_code = 200  # idempotent success, not a client error (LAW-008)
    error_code = "duplicate_event"


class AuthenticationError(KbError):
    status_code = 401
    error_code = "unauthenticated"


class AuthorizationError(KbError):
    status_code = 403
    error_code = "forbidden"


class NamespaceError(AuthorizationError):
    error_code = "namespace_not_allowed"


class NotFoundError(KbError):
    status_code = 404
    error_code = "not_found"


class GraphUnavailableError(KbError):
    status_code = 503
    error_code = "graph_unavailable"


class QuarantineError(KbError):
    status_code = 422
    error_code = "ontology_quarantine"

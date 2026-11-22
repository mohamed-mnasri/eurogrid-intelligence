"""Service-layer errors that the API maps to HTTP status codes.

Services raise these instead of HTTP exceptions so the same code works from the
CLI, tests, the API and the Phase 9 agent.
"""


class ServiceError(Exception):
    """Base class; ``status_code`` is the HTTP status the API should return."""

    status_code = 500


class NotFoundError(ServiceError):
    """A requested record (anomaly, report, evidence, simulation) does not exist."""

    status_code = 404


class InvalidRequestError(ServiceError):
    """Inputs are well-formed but not allowed (unknown zone, range too long, ...)."""

    status_code = 422


class UnavailableError(ServiceError):
    """A dependency (database, provider) is missing or failed."""

    status_code = 503

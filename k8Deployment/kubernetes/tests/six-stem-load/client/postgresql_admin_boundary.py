"""Read PostgreSQL administrator connection settings for the lifecycle broker.

This module defines the credential boundary for the broker's transactional
observer-role bootstrap. It does not open a connection, execute SQL, spawn a
client process, print configuration, generate a password, or write a Secret.
The Job injects its two secret-backed variables into the broker
container only; container environment is per-container even when containers
share an image and an ``emptyDir`` volume.

Connection coordinates are fixed to the private PostgreSQL Service and the
local application database. Keeping them outside the Secret makes the expected
network destination reviewable in the suspended Job manifest while the
password remains only in an ignored local Secret and the broker process.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import re
from typing import Mapping


_EXPECTED_POSTGRESQL_HOST = "clouddsp-postgresql.clouddsp-data.svc"
_EXPECTED_POSTGRESQL_PORT = 5432
_EXPECTED_POSTGRESQL_DATABASE = "clouddsp_job_api"
_POSTGRESQL_USERNAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,62}$")
_MAX_CREDENTIAL_LENGTH = 1024


class PostgreSQLAdminBoundaryError(RuntimeError):
    """A safe configuration error that never includes a credential value."""


@dataclass(frozen=True)
class PostgreSQLAdminConnectionSettings:
    """Private-Service connection settings held only by the broker process.

    The username and password are excluded from the generated representation
    to make accidental debug logging safer. Callers still must never log this
    object or pass its credential fields to the unprivileged load client.
    """

    host: str
    port: int
    database: str
    username: str = field(repr=False)
    password: str = field(repr=False)

    def __post_init__(self) -> None:
        """Keep even manually constructed settings on the fixed local endpoint."""

        if self.host != _EXPECTED_POSTGRESQL_HOST:
            raise PostgreSQLAdminBoundaryError(
                "PostgreSQL administrator host must be the reviewed private Service"
            )
        if (
            not isinstance(self.port, int)
            or isinstance(self.port, bool)
            or self.port != _EXPECTED_POSTGRESQL_PORT
        ):
            raise PostgreSQLAdminBoundaryError("PostgreSQL administrator port was not reviewed")
        if self.database != _EXPECTED_POSTGRESQL_DATABASE:
            raise PostgreSQLAdminBoundaryError("PostgreSQL administrator database was not reviewed")
        if not isinstance(self.username, str) or not _POSTGRESQL_USERNAME_PATTERN.fullmatch(
            self.username
        ):
            raise PostgreSQLAdminBoundaryError("PostgreSQL administrator username was invalid")
        if (
            not isinstance(self.password, str)
            or not self.password
            or len(self.password) > _MAX_CREDENTIAL_LENGTH
        ):
            raise PostgreSQLAdminBoundaryError("PostgreSQL administrator password was invalid")

    @classmethod
    def from_environment(
        cls, *, environ: Mapping[str, str] | None = None
    ) -> "PostgreSQLAdminConnectionSettings":
        """Read only the dedicated broker variables and pin the network target.

        The injectable mapping is useful for an offline caller or future
        library integration. Production code uses the broker container's
        environment, populated by Secret references in that container alone.
        No PostgreSQL driver or client is imported here.
        """

        source = os.environ if environ is None else environ
        host = _required_value(source, "SIX_STEM_POSTGRESQL_ADMIN_HOST")
        port_text = _required_value(source, "SIX_STEM_POSTGRESQL_ADMIN_PORT")
        database = _required_value(source, "SIX_STEM_POSTGRESQL_ADMIN_DATABASE")
        username = _required_value(source, "SIX_STEM_POSTGRESQL_ADMIN_USERNAME")
        password = _required_value(source, "SIX_STEM_POSTGRESQL_ADMIN_PASSWORD")

        if port_text != str(_EXPECTED_POSTGRESQL_PORT):
            raise PostgreSQLAdminBoundaryError("PostgreSQL administrator port was not reviewed")

        return cls(
            host=host,
            port=_EXPECTED_POSTGRESQL_PORT,
            database=database,
            username=username,
            password=password,
        )


def _required_value(environ: Mapping[str, str], name: str) -> str:
    """Require one nonempty, bounded value without echoing it in exceptions."""

    value = environ.get(name)
    if not isinstance(value, str) or not value:
        raise PostgreSQLAdminBoundaryError(f"required broker setting {name} was absent")
    if len(value) > _MAX_CREDENTIAL_LENGTH:
        raise PostgreSQLAdminBoundaryError(f"broker setting {name} exceeded its bound")
    return value

"""Provision and revoke one temporary, aggregate-only PostgreSQL observer.

This adapter is called only by the lifecycle broker after it has independently
verified the three Jobs through the ordinary owner-bound API. It creates the
role and its run-bound SECURITY DEFINER function in one short administrator
transaction, inspects the committed-in-transaction permissions before
returning any observer credential, and never prints SQL or passwords.

The raw observer password is generated in memory. Only a SCRAM-SHA-256
verifier is sent to PostgreSQL, which keeps the raw credential out of SQL
logs. The raw value is later handed to a separate memory-backed volume that
the authenticated load client does not mount.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
import re

from observer_capability_contract import VerifiedLoadObserverCapabilityContract
from postgresql_admin_boundary import PostgreSQLAdminConnectionSettings
from postgresql_observer_contract import (
    PostgreSQLObserverAccessContract,
    build_postgresql_observer_access_contract,
)
from lifecycle_handoff import TemporaryPostgreSQLObserverCredentials


_CONNECT_TIMEOUT_SECONDS = 5
_SCRAM_ITERATIONS = 4096
_ROLE_PREFIX = "clouddsp_six_stem_observer_"
_FUNCTION_PREFIX = "clouddsp_six_stem_observe_"
_RUN_MARKER_PATTERN = re.compile(r"^[a-z0-9]{8,24}$")


class PostgreSQLObserverBootstrapError(RuntimeError):
    """A safe failure category that excludes passwords, SQL, and private IDs."""


@dataclass(frozen=True)
class PostgreSQLObserverBootstrap:
    """Administrator-scoped adapter owned exclusively by the broker container."""

    settings: PostgreSQLAdminConnectionSettings = field(repr=False)

    def provision(
        self, *, capability: VerifiedLoadObserverCapabilityContract
    ) -> TemporaryPostgreSQLObserverCredentials:
        """Atomically create, grant, and verify the run-specific observer.

        The exact Job/owner literals come only from the in-memory capability
        contract. Role creation, minimal database/schema grants, function
        definition, PUBLIC-execute revocation, the one explicit function grant,
        and privilege verification all live in one transaction. A failure
        before commit rolls back every database object and leaves no observer
        credential handoff.
        """

        try:
            contract = build_postgresql_observer_access_contract(capability=capability)
        except Exception:
            raise PostgreSQLObserverBootstrapError(
                "PostgreSQL observer capability contract was rejected"
            ) from None
        password = secrets.token_urlsafe(36)
        password_verifier = _scram_sha_256_verifier(password)

        try:
            psycopg, sql = _psycopg_modules()
            with psycopg.connect(
                host=self.settings.host,
                port=self.settings.port,
                dbname=self.settings.database,
                user=self.settings.username,
                password=self.settings.password,
                connect_timeout=_CONNECT_TIMEOUT_SECONDS,
                application_name="clouddsp-six-stem-observer-bootstrap",
            ) as connection:
                with connection.transaction():
                    # Quote identifiers with Psycopg's SQL composer and pass
                    # only a precomputed SCRAM verifier as a SQL literal. The
                    # raw generated password never traverses the network in
                    # a CREATE ROLE statement or appears in PostgreSQL logs.
                    connection.execute(
                        sql.SQL(
                            "CREATE ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB "
                            "NOCREATEROLE NOREPLICATION NOINHERIT "
                            "CONNECTION LIMIT 1 PASSWORD {}"
                        ).format(
                            sql.Identifier(contract.role_name),
                            sql.Literal(password_verifier),
                        )
                    )
                    # This reviewed definition creates the no-argument
                    # aggregate function, removes PostgreSQL's default PUBLIC
                    # EXECUTE grant, and grants this role that one function.
                    connection.execute(contract.definition_sql)

                    # Check the actual catalog grants before this transaction
                    # can commit or the raw password can cross a volume
                    # boundary. Any mismatch raises and rolls the transaction
                    # back as a unit.
                    _verify_observer_permissions(
                        connection=connection,
                        contract=contract,
                        administrator_username=self.settings.username,
                    )
        except PostgreSQLObserverBootstrapError:
            raise
        except Exception as error:
            # Driver diagnostics may contain SQL text or local connection
            # details. Preserve only the safe category for the Pod log.
            raise PostgreSQLObserverBootstrapError(
                "PostgreSQL observer transaction or grant verification failed"
            ) from None

        return TemporaryPostgreSQLObserverCredentials(
            run_marker=contract.run_marker,
            username=contract.role_name,
            password=password,
            function_schema=contract.function_schema,
            function_name=contract.function_name,
        )

    def revoke(self, *, credentials: TemporaryPostgreSQLObserverCredentials) -> None:
        """Remove only this contract's function, grants, and temporary role.

        Cleanup uses the opaque run marker to reconstruct strict generated
        identifiers. It revokes direct privileges from only that temporary
        role before dropping it; it does not delete any Job, task, outbox, or
        user-owned application row.
        """

        contract = _contract_from_credentials(credentials)
        try:
            psycopg, sql = _psycopg_modules()
            quoted_role = sql.Identifier(contract.role_name)
            quoted_database = sql.Identifier(self.settings.database)
            quoted_schema = sql.Identifier(contract.function_schema)
            quoted_function = sql.Identifier(contract.function_name)
            with psycopg.connect(
                host=self.settings.host,
                port=self.settings.port,
                dbname=self.settings.database,
                user=self.settings.username,
                password=self.settings.password,
                connect_timeout=_CONNECT_TIMEOUT_SECONDS,
                application_name="clouddsp-six-stem-observer-cleanup",
            ) as connection:
                with connection.transaction():
                    # Every target is reconstructed from the fixed prefix and
                    # validated marker. These statements cannot widen cleanup
                    # to another run or to application data.
                    connection.execute(
                        sql.SQL("DROP FUNCTION IF EXISTS {}.{}()").format(
                            quoted_schema, quoted_function
                        )
                    )
                    connection.execute(
                        sql.SQL("REVOKE ALL PRIVILEGES ON DATABASE {} FROM {}").format(
                            quoted_database, quoted_role
                        )
                    )
                    connection.execute(
                        sql.SQL("REVOKE ALL PRIVILEGES ON SCHEMA public FROM {}").format(
                            quoted_role
                        )
                    )
                    connection.execute(
                        sql.SQL(
                            "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {}"
                        ).format(quoted_role)
                    )
                    connection.execute(
                        sql.SQL(
                            "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM {}"
                        ).format(quoted_role)
                    )
                    connection.execute(
                        sql.SQL(
                            "REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM {}"
                        ).format(quoted_role)
                    )
                    connection.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(quoted_role))
        except Exception:
            # Do not print the failed SQL or PostgreSQL's exception text.
            raise PostgreSQLObserverBootstrapError(
                "PostgreSQL observer cleanup failed"
            ) from None


def _verify_observer_permissions(
    *, connection: object, contract: PostgreSQLObserverAccessContract,
    administrator_username: str,
) -> None:
    """Fail closed unless the catalogs show the exact limited role/function."""

    role_row = connection.execute(
        """SELECT oid, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
                  rolreplication, rolinherit, rolconnlimit
             FROM pg_catalog.pg_roles
            WHERE rolname = %s""",
        (contract.role_name,),
    ).fetchone()
    if role_row is None:
        raise PostgreSQLObserverBootstrapError("temporary observer role was absent")
    role_oid, can_login, is_superuser, can_create_database, can_create_roles, can_replicate, inherits, connection_limit = role_row
    if (
        can_login is not True
        or is_superuser is not False
        or can_create_database is not False
        or can_create_roles is not False
        or can_replicate is not False
        or inherits is not False
        or connection_limit != 1
    ):
        raise PostgreSQLObserverBootstrapError("temporary observer role attributes were broader than reviewed")

    # A role with an unexpected membership could inherit privileges unrelated
    # to this observer contract, so both granted memberships and members of
    # this role must be absent.
    membership_count = connection.execute(
        """SELECT COUNT(*)
             FROM pg_catalog.pg_auth_members
            WHERE roleid = %s OR member = %s""",
        (role_oid, role_oid),
    ).fetchone()[0]
    if membership_count != 0:
        raise PostgreSQLObserverBootstrapError("temporary observer role had a membership")

    database_grants = _direct_grants(
        connection=connection,
        query="""SELECT acl.privilege_type, acl.is_grantable
                   FROM pg_catalog.pg_database AS db_entry
                   CROSS JOIN LATERAL pg_catalog.aclexplode(
                     COALESCE(db_entry.datacl,
                              pg_catalog.acldefault('d', db_entry.datdba))) AS acl
                  WHERE db_entry.datname = current_database()
                    AND acl.grantee = %s""",
        role_oid=role_oid,
    )
    schema_grants = _direct_grants(
        connection=connection,
        query="""SELECT acl.privilege_type, acl.is_grantable
                   FROM pg_catalog.pg_namespace AS namespace
                   CROSS JOIN LATERAL pg_catalog.aclexplode(
                     COALESCE(namespace.nspacl,
                              pg_catalog.acldefault('n', namespace.nspowner))) AS acl
                  WHERE namespace.nspname = 'public'
                    AND acl.grantee = %s""",
        role_oid=role_oid,
    )
    if database_grants != {("CONNECT", False)} or schema_grants != {("USAGE", False)}:
        raise PostgreSQLObserverBootstrapError("temporary observer database or schema grants were broader than reviewed")

    effective_connection = connection.execute(
        """SELECT pg_catalog.has_database_privilege(%s, current_database(), 'CONNECT'),
                  pg_catalog.has_database_privilege(%s, current_database(), 'CREATE'),
                  pg_catalog.has_schema_privilege(%s, 'public', 'USAGE')""",
        (contract.role_name, contract.role_name, contract.role_name),
    ).fetchone()
    if effective_connection != (True, False, True):
        raise PostgreSQLObserverBootstrapError("temporary observer inherited broader database privileges")

    function_row = connection.execute(
        """SELECT proc.proowner,
                  proc.prosecdef,
                  proc.pronargs,
                  proc.proconfig,
                  pg_catalog.pg_get_userbyid(proc.proowner),
                  EXISTS (
                    SELECT 1
                      FROM pg_catalog.aclexplode(
                        COALESCE(proc.proacl,
                                 pg_catalog.acldefault('f', proc.proowner))) AS acl
                     WHERE acl.grantee = 0
                       AND acl.privilege_type = 'EXECUTE'
                  )
             FROM pg_catalog.pg_proc AS proc
             JOIN pg_catalog.pg_namespace AS namespace
               ON namespace.oid = proc.pronamespace
            WHERE namespace.nspname = %s
              AND proc.proname = %s""",
        (contract.function_schema, contract.function_name),
    ).fetchone()
    if function_row is None:
        raise PostgreSQLObserverBootstrapError("temporary observer function was absent")
    function_owner_oid, security_definer, argument_count, function_config, function_owner_name, public_execute = function_row
    if (
        security_definer is not True
        or argument_count != 0
        or function_owner_name != administrator_username
        or not function_config
        or "search_path=pg_catalog, public" not in function_config
        or public_execute is True
    ):
        raise PostgreSQLObserverBootstrapError("temporary observer function boundary was broader than reviewed")

    function_grants = _direct_grants(
        connection=connection,
        query="""SELECT acl.privilege_type, acl.is_grantable
                   FROM pg_catalog.pg_proc AS proc
                   JOIN pg_catalog.pg_namespace AS namespace
                     ON namespace.oid = proc.pronamespace
                   CROSS JOIN LATERAL pg_catalog.aclexplode(
                     COALESCE(proc.proacl,
                              pg_catalog.acldefault('f', proc.proowner))) AS acl
                  WHERE namespace.nspname = %s
                    AND proc.proname = %s
                    AND acl.grantee = %s""",
        role_oid=role_oid,
        extra_parameters=(contract.function_schema, contract.function_name),
    )
    if function_grants != {("EXECUTE", False)}:
        raise PostgreSQLObserverBootstrapError("temporary observer function grant was not exact")

    other_direct_function_grants = connection.execute(
        """SELECT EXISTS (
             SELECT 1
               FROM pg_catalog.pg_proc AS proc
               JOIN pg_catalog.pg_namespace AS namespace
                 ON namespace.oid = proc.pronamespace
               CROSS JOIN LATERAL pg_catalog.aclexplode(
                 COALESCE(proc.proacl,
                          pg_catalog.acldefault('f', proc.proowner))) AS acl
              WHERE acl.grantee = %s
                AND NOT (namespace.nspname = %s AND proc.proname = %s)
           )""",
        (role_oid, contract.function_schema, contract.function_name),
    ).fetchone()[0]
    if other_direct_function_grants:
        raise PostgreSQLObserverBootstrapError("temporary observer had another function grant")

    # Verify no direct/effective data privilege has appeared on any table or
    # sequence in the application schema. PostgreSQL's normal PUBLIC baseline
    # remains unchanged; this broker never alters global PUBLIC ACLs.
    table_privilege = connection.execute(
        """SELECT EXISTS (
             SELECT 1
               FROM pg_catalog.pg_class AS relation
               JOIN pg_catalog.pg_namespace AS namespace
                 ON namespace.oid = relation.relnamespace
              WHERE namespace.nspname = 'public'
                AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
                AND (
                  pg_catalog.has_table_privilege(%s, relation.oid, 'SELECT') OR
                  pg_catalog.has_table_privilege(%s, relation.oid, 'INSERT') OR
                  pg_catalog.has_table_privilege(%s, relation.oid, 'UPDATE') OR
                  pg_catalog.has_table_privilege(%s, relation.oid, 'DELETE') OR
                  pg_catalog.has_table_privilege(%s, relation.oid, 'TRUNCATE') OR
                  pg_catalog.has_table_privilege(%s, relation.oid, 'REFERENCES') OR
                  pg_catalog.has_table_privilege(%s, relation.oid, 'TRIGGER') OR
                  pg_catalog.has_any_column_privilege(%s, relation.oid, 'SELECT') OR
                  pg_catalog.has_any_column_privilege(%s, relation.oid, 'INSERT') OR
                  pg_catalog.has_any_column_privilege(%s, relation.oid, 'UPDATE') OR
                  pg_catalog.has_any_column_privilege(%s, relation.oid, 'REFERENCES')
                )
           )""",
        (contract.role_name,) * 11,
    ).fetchone()[0]
    sequence_privilege = connection.execute(
        """SELECT EXISTS (
             SELECT 1
               FROM pg_catalog.pg_class AS seq
               JOIN pg_catalog.pg_namespace AS namespace
                 ON namespace.oid = seq.relnamespace
              WHERE namespace.nspname = 'public'
                AND seq.relkind = 'S'
                AND (
                  pg_catalog.has_sequence_privilege(%s, seq.oid, 'USAGE') OR
                  pg_catalog.has_sequence_privilege(%s, seq.oid, 'SELECT') OR
                  pg_catalog.has_sequence_privilege(%s, seq.oid, 'UPDATE')
                )
           )""",
        (contract.role_name,) * 3,
    ).fetchone()[0]
    schema_create = connection.execute(
        "SELECT pg_catalog.has_schema_privilege(%s, 'public', 'CREATE')",
        (contract.role_name,),
    ).fetchone()[0]
    if table_privilege or sequence_privilege or schema_create:
        raise PostgreSQLObserverBootstrapError("temporary observer inherited data or DDL privileges")

    # Referencing the OID explicitly above makes it clear the inspected
    # SECURITY DEFINER function is owned by the connected administrator, not
    # the limited role. Keep the local variable in the same reviewed check.
    if function_owner_oid != connection.execute(
        "SELECT oid FROM pg_catalog.pg_roles WHERE rolname = %s",
        (administrator_username,),
    ).fetchone()[0]:
        raise PostgreSQLObserverBootstrapError("temporary observer function owner was unexpected")


def _direct_grants(
    *, connection: object, query: str, role_oid: int,
    extra_parameters: tuple[object, ...] = (),
) -> set[tuple[str, bool]]:
    """Collect only ACL entries explicitly granted to the temporary role."""

    return {
        (privilege, grantable)
        for privilege, grantable in connection.execute(
            query, (*extra_parameters, role_oid) if extra_parameters else (role_oid,)
        ).fetchall()
    }


def _contract_from_credentials(
    credentials: TemporaryPostgreSQLObserverCredentials,
) -> PostgreSQLObserverAccessContract:
    """Rebuild generated database identifiers without trusting file text."""

    if (
        not isinstance(credentials, TemporaryPostgreSQLObserverCredentials)
        or not isinstance(credentials.run_marker, str)
        or not _RUN_MARKER_PATTERN.fullmatch(credentials.run_marker)
    ):
        raise PostgreSQLObserverBootstrapError("temporary observer cleanup coordinate was invalid")
    expected_role = f"{_ROLE_PREFIX}{credentials.run_marker}"
    expected_function = f"{_FUNCTION_PREFIX}{credentials.run_marker}"
    if (
        credentials.username != expected_role
        or credentials.function_schema != "public"
        or credentials.function_name != expected_function
    ):
        raise PostgreSQLObserverBootstrapError("temporary observer cleanup coordinate was invalid")
    return PostgreSQLObserverAccessContract(
        run_marker=credentials.run_marker,
        role_name=expected_role,
        function_schema="public",
        function_name=expected_function,
        owner_subject="",
        job_ids=(),
        definition_sql="",
    )


def _scram_sha_256_verifier(password: str) -> str:
    """Create PostgreSQL's standard SCRAM verifier without transmitting password."""

    salt = secrets.token_bytes(16)
    salted_password = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _SCRAM_ITERATIONS
    )
    client_key = hmac.new(salted_password, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted_password, b"Server Key", hashlib.sha256).digest()
    encoded_salt = base64.b64encode(salt).decode("ascii")
    encoded_stored_key = base64.b64encode(stored_key).decode("ascii")
    encoded_server_key = base64.b64encode(server_key).decode("ascii")
    return (
        f"SCRAM-SHA-256${_SCRAM_ITERATIONS}:{encoded_salt}"
        f"${encoded_stored_key}:{encoded_server_key}"
    )


def _psycopg_modules() -> tuple[object, object]:
    """Import the hash-pinned database client only inside this broker adapter."""

    try:
        import psycopg
        from psycopg import sql
    except ImportError as error:
        raise PostgreSQLObserverBootstrapError("pinned PostgreSQL client was unavailable") from None
    return psycopg, sql

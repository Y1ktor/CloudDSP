"""Define—not execute—the exact PostgreSQL observer boundary for one load run.

This module turns the prior in-memory verified-coordinate capability contract
into a reviewable SQL *definition*. It performs no PostgreSQL connection,
role/function creation, password generation, Secret write, subprocess call, or
Kubernetes operation. A later administrator-only bootstrap task must decide
whether and how to execute this definition after it has introduced a separate
temporary credential channel.

The future role receives only ``CONNECT``, ``USAGE`` on ``public``, and
``EXECUTE`` on one zero-argument ``SECURITY DEFINER`` function. The function
embeds the exact three verified UUIDs and temporary owner as SQL literals and
returns aggregate counts/status maps plus the three already owner-verified
Jobs' exact object keys and hashes for a separate MinIO read-only observer. It
accepts no caller-supplied Job ID, prefix, owner, filter, SQL fragment, or
arbitrary array; the fixed output cannot be widened by its caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from uuid import UUID

from observer_capability_contract import (
    ExactLoadObjectScope,
    VerifiedLoadObserverCapabilityContract,
)


_LOAD_JOB_COUNT = 3
_STEM_MODE = "6-stems"
_UPLOADS_BUCKET = "clouddsp-uploads"
_POSTGRESQL_DATABASE = "clouddsp_job_api"
_RUN_MARKER_PATTERN = re.compile(r"^[a-z0-9]{8,24}$")
_ROLE_PREFIX = "clouddsp_six_stem_observer_"
_FUNCTION_PREFIX = "clouddsp_six_stem_observe_"
_FUNCTION_SCHEMA = "public"


class PostgreSQLObserverContractError(RuntimeError):
    """A safe contract-rejection category with no private values in its text."""


@dataclass(frozen=True)
class PostgreSQLObserverAccessContract:
    """One future temporary role/function pair, without a password or execution.

    ``definition_sql`` is deliberately absent from normal representations
    because it contains the private owner and three Job UUIDs as fixed SQL
    literals. It is only an administrator-side input to a future bootstrap;
    the load client and the observer never receive it.
    """

    run_marker: str
    role_name: str
    function_schema: str
    function_name: str
    owner_subject: str = field(repr=False)
    job_ids: tuple[str, ...] = field(repr=False)
    definition_sql: str = field(repr=False)

    @property
    def qualified_function_name(self) -> str:
        """Return the schema-qualified function identifier for a reviewed grant."""

        return f"{self.function_schema}.{self.function_name}"

    @property
    def role_attributes(self) -> tuple[str, ...]:
        """Expose the finite attributes a later bootstrap must enforce exactly."""

        return (
            "LOGIN",
            "NOSUPERUSER",
            "NOCREATEDB",
            "NOCREATEROLE",
            "NOREPLICATION",
            "NOINHERIT",
            "CONNECTION LIMIT 1",
        )

    @property
    def permitted_grants(self) -> tuple[str, ...]:
        """Name the whole future grant set; anything else is a design violation."""

        return (
            "CONNECT on the CloudDSP application database",
            "USAGE on schema public",
            f"EXECUTE on function {self.qualified_function_name}()",
        )


def build_postgresql_observer_access_contract(
    *, capability: VerifiedLoadObserverCapabilityContract
) -> PostgreSQLObserverAccessContract:
    """Build the single-role, single-function definition from verified coordinates.

    This revalidates the previous pure contract before rendering SQL. That
    prevents a future bootstrap refactor from changing an object prefix, owner,
    or Job list after the broker's proof but before a privileged database step.
    """

    _validate_capability(capability)
    role_name = f"{_ROLE_PREFIX}{capability.run_marker}"
    function_name = f"{_FUNCTION_PREFIX}{capability.run_marker}"
    _validate_identifier(role_name, purpose="temporary observer role name")
    _validate_identifier(function_name, purpose="temporary observer function name")
    definition_sql = _render_aggregate_observer_definition(
        role_name=role_name,
        function_name=function_name,
        owner_subject=capability.owner_subject,
        job_ids=capability.job_ids,
        object_scopes=capability.object_scopes,
    )
    return PostgreSQLObserverAccessContract(
        run_marker=capability.run_marker,
        role_name=role_name,
        function_schema=_FUNCTION_SCHEMA,
        function_name=function_name,
        owner_subject=capability.owner_subject,
        job_ids=capability.job_ids,
        definition_sql=definition_sql,
    )


def _validate_capability(capability: object) -> None:
    """Accept only the prior exact-object contract, never a generic UUID list."""

    if not isinstance(capability, VerifiedLoadObserverCapabilityContract):
        raise PostgreSQLObserverContractError("PostgreSQL observer input was not verified capability data")
    if not isinstance(capability.run_marker, str) or not _RUN_MARKER_PATTERN.fullmatch(
        capability.run_marker
    ):
        raise PostgreSQLObserverContractError("PostgreSQL observer run marker was invalid")
    if capability.stem_mode != _STEM_MODE:
        raise PostgreSQLObserverContractError("PostgreSQL observer stem mode was invalid")
    _canonical_uuid(capability.owner_subject, purpose="PostgreSQL observer owner subject")
    if not isinstance(capability.job_ids, tuple) or len(capability.job_ids) != _LOAD_JOB_COUNT:
        raise PostgreSQLObserverContractError("PostgreSQL observer Job count was invalid")
    if not isinstance(capability.object_scopes, tuple) or len(capability.object_scopes) != _LOAD_JOB_COUNT:
        raise PostgreSQLObserverContractError("PostgreSQL observer object scope count was invalid")

    # Validate the entire identifier set before comparing object scopes. That
    # produces a deterministic narrow error category for a duplicate-ID attack
    # rather than letting the first mismatched derived object path obscure it.
    observed_ids = [
        _canonical_uuid(job_id, purpose="PostgreSQL observer Job identifier")
        for job_id in capability.job_ids
    ]
    if len(set(observed_ids)) != _LOAD_JOB_COUNT:
        raise PostgreSQLObserverContractError("PostgreSQL observer Job identifiers were duplicated")

    for ordinal, (canonical_job_id, scope) in enumerate(
        zip(observed_ids, capability.object_scopes, strict=True), start=1
    ):
        _validate_scope(
            scope=scope,
            job_id=canonical_job_id,
            run_marker=capability.run_marker,
            ordinal=ordinal,
        )


def _validate_scope(
    *, scope: object, job_id: str, run_marker: str, ordinal: int
) -> None:
    """Ensure database observation remains paired with the already-reviewed object scope."""

    if not isinstance(scope, ExactLoadObjectScope):
        raise PostgreSQLObserverContractError("PostgreSQL observer object scope was invalid")
    expected_filename = f"six-stem-load-{run_marker}-{ordinal}.wav"
    if (
        scope.job_id != job_id
        or scope.bucket != _UPLOADS_BUCKET
        or scope.source_object_key != f"uploads/{job_id}/{expected_filename}"
        or scope.source_filename != expected_filename
        or type(scope.source_size_bytes) is not int
        or not 1 <= scope.source_size_bytes <= 256 * 1024 * 1024
        or not isinstance(scope.source_sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", scope.source_sha256)
        or scope.stems_prefix != f"stems/{job_id}/"
        or scope.midi_prefix != f"midi/{job_id}/"
    ):
        raise PostgreSQLObserverContractError("PostgreSQL observer object scope was invalid")


def _render_aggregate_observer_definition(
    *,
    role_name: str,
    function_name: str,
    owner_subject: str,
    job_ids: tuple[str, ...],
    object_scopes: tuple[ExactLoadObjectScope, ...],
) -> str:
    """Render a no-argument aggregate-only function plus its exact SQL grants.

    UUID and subject values have passed strict canonical validation before this
    point. They are still rendered as SQL literals rather than parameters so
    the resulting zero-argument function cannot be widened by its caller.
    """

    job_array = ", ".join(f"{_sql_literal(job_id)}::uuid" for job_id in job_ids)
    expected_sources = ",\n    ".join(
        "("
        f"{_sql_literal(scope.job_id)}::uuid, {ordinal}, "
        f"{_sql_literal(scope.source_sha256)}"
        ")"
        for ordinal, scope in enumerate(object_scopes, start=1)
    )
    subject_literal = _sql_literal(owner_subject)
    quoted_schema = _quoted_identifier(_FUNCTION_SCHEMA)
    quoted_function = _quoted_identifier(function_name)
    quoted_role = _quoted_identifier(role_name)
    qualified_function = f"{quoted_schema}.{quoted_function}"
    quoted_database = _quoted_identifier(_POSTGRESQL_DATABASE)
    return f"""-- Design-only six-stem observer definition. A later administrator-only
-- bootstrap may execute it after creating a fresh ignored local password
-- Secret. Do not send this SQL to the load client or observer container.
CREATE FUNCTION {qualified_function}()
RETURNS TABLE (
  observed_job_count INTEGER,
  source_uploaded_count INTEGER,
  job_status_counts JSONB,
  demucs_succeeded_count INTEGER,
  basic_pitch_succeeded_count INTEGER,
  adtof_succeeded_count INTEGER,
  task_failure_count INTEGER,
  active_task_lease_count INTEGER,
  task_status_counts JSONB,
  outbox_delivery_counts JSONB,
  artifact_evidence JSONB
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $clouddsp_six_stem_observer$
WITH expected_sources(job_id, load_ordinal, source_sha256) AS (
  VALUES
    {expected_sources}
),
verified_jobs AS (
  SELECT j.job_id, j.status, j.source_uploaded, j.input_bucket,
         j.input_object_key, j.source_filename, j.source_content_type,
         j.source_size_bytes, j.stem_mode, j.stems, j.midi,
         expected_sources.load_ordinal, expected_sources.source_sha256
  FROM public.jobs AS j
  INNER JOIN expected_sources ON expected_sources.job_id = j.job_id
  WHERE j.job_id = ANY (ARRAY[{job_array}])
    AND j.owner_sub = {subject_literal}
    AND j.source_type = 'direct_upload'
    AND j.stem_mode = '6-stems'
),
job_statuses AS (
  SELECT status, COUNT(*)::INTEGER AS status_count
  FROM verified_jobs
  GROUP BY status
),
filtered_tasks AS (
  SELECT t.stage, t.status, t.lease_token
  FROM public.processing_tasks AS t
  INNER JOIN verified_jobs AS j ON j.job_id = t.job_id
),
task_statuses AS (
  SELECT stage || ':' || status AS status_key, COUNT(*)::INTEGER AS status_count
  FROM filtered_tasks
  GROUP BY stage, status
),
filtered_outbox AS (
  SELECT e.stage, e.event_type, e.publication_status
  FROM public.outbox_events AS e
  INNER JOIN verified_jobs AS j ON j.job_id = e.job_id
),
outbox_statuses AS (
  SELECT stage || ':' || event_type || ':' || publication_status AS status_key,
         COUNT(*)::INTEGER AS status_count
  FROM filtered_outbox
  GROUP BY stage, event_type, publication_status
)
SELECT
  (SELECT COUNT(*)::INTEGER FROM verified_jobs),
  (SELECT COUNT(*) FILTER (WHERE source_uploaded)::INTEGER FROM verified_jobs),
  COALESCE((SELECT jsonb_object_agg(status, status_count) FROM job_statuses), '{{}}'::jsonb),
  (SELECT COUNT(*) FILTER (WHERE stage = 'demucs' AND status = 'succeeded')::INTEGER FROM filtered_tasks),
  (SELECT COUNT(*) FILTER (WHERE stage = 'basic-pitch' AND status = 'succeeded')::INTEGER FROM filtered_tasks),
  (SELECT COUNT(*) FILTER (WHERE stage = 'adtof' AND status = 'succeeded')::INTEGER FROM filtered_tasks),
  (SELECT COUNT(*) FILTER (WHERE status = 'failed')::INTEGER FROM filtered_tasks),
  (SELECT COUNT(*) FILTER (WHERE lease_token IS NOT NULL)::INTEGER FROM filtered_tasks),
  COALESCE((SELECT jsonb_object_agg(status_key, status_count) FROM task_statuses), '{{}}'::jsonb),
  COALESCE((SELECT jsonb_object_agg(status_key, status_count) FROM outbox_statuses), '{{}}'::jsonb),
  COALESCE((
    SELECT jsonb_agg(
      jsonb_build_object(
        'job_id', job_id::text,
        'input_bucket', input_bucket,
        'input_object_key', input_object_key,
        'source_filename', source_filename,
        'source_content_type', source_content_type,
        'source_size_bytes', source_size_bytes,
        'source_sha256', source_sha256,
        'load_ordinal', load_ordinal,
        'stem_mode', stem_mode,
        'stems', COALESCE((
          SELECT jsonb_object_agg(
            stem_name,
            jsonb_build_object(
              'bucket', artifact->>'bucket',
              'object_key', artifact->>'s3_key',
              'content_type', artifact->>'content_type',
              'size_bytes', artifact->'size_bytes',
              'sha256', artifact->>'sha256'
            )
          )
          FROM jsonb_each(stems) AS stem_entries(stem_name, artifact)
        ), '{{}}'::jsonb),
        'midi', CASE WHEN midi ? 'drums' THEN jsonb_build_object(
          'drums', jsonb_build_object(
            'status', midi #> '{{drums,status}}',
            'extractor', midi #> '{{drums,extractor}}',
            's3_key', midi #> '{{drums,s3_key}}',
            'bpm_key', midi #> '{{drums,bpm_key}}'
          )
        ) ELSE '{{}}'::jsonb END
      ) ORDER BY load_ordinal
    ) FROM verified_jobs
  ), '[]'::jsonb);
$clouddsp_six_stem_observer$;

-- A future bootstrap must run as the database administrator, never this role.
-- Grant only the connection and name-resolution privileges needed to invoke
-- this one function, then remove PostgreSQL's default PUBLIC execute grant
-- before making its one explicit execute grant. No table, sequence,
-- schema-create, DDL, role, or cleanup privilege is part of this boundary.
GRANT CONNECT ON DATABASE {quoted_database} TO {quoted_role};
GRANT USAGE ON SCHEMA {quoted_schema} TO {quoted_role};
REVOKE ALL ON FUNCTION {qualified_function}() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION {qualified_function}() TO {quoted_role};
"""


def _canonical_uuid(value: object, *, purpose: str) -> str:
    """Require canonical UUID text without including received text in errors."""

    if not isinstance(value, str):
        raise PostgreSQLObserverContractError(f"{purpose} was invalid")
    try:
        canonical = str(UUID(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise PostgreSQLObserverContractError(f"{purpose} was invalid") from error
    if canonical != value:
        raise PostgreSQLObserverContractError(f"{purpose} was invalid")
    return canonical


def _validate_identifier(value: str, *, purpose: str) -> None:
    """Keep generated database identifiers legal and shorter than PostgreSQL's cap."""

    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value):
        raise PostgreSQLObserverContractError(f"{purpose} was invalid")


def _quoted_identifier(value: str) -> str:
    """Quote one already-validated identifier for a future SQL statement."""

    return f'"{value}"'


def _sql_literal(value: str) -> str:
    """Quote one canonical UUID/subject literal without accepting arbitrary SQL."""

    return "'" + value.replace("'", "''") + "'"

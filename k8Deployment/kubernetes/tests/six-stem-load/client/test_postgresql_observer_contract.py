"""Offline tests for the future aggregate-only PostgreSQL observer definition."""

from __future__ import annotations

from dataclasses import replace
import unittest

from observer_capability_contract import derive_verified_load_observer_capability_contract
from postgresql_observer_contract import (
    PostgreSQLObserverContractError,
    build_postgresql_observer_access_contract,
)
from test_observer_capability_contract import _JOB_IDS, _SUBJECT, proof


class PostgreSQLObserverContractTests(unittest.TestCase):
    """Keep the later temporary role narrow before an administrator can create it."""

    def test_definition_has_one_zero_argument_aggregate_function_and_exact_grant(self) -> None:
        """The role cannot choose IDs, see raw rows, or inherit other database rights."""

        capability = derive_verified_load_observer_capability_contract(proof=proof())
        contract = build_postgresql_observer_access_contract(capability=capability)

        self.assertEqual(contract.role_name, "clouddsp_six_stem_observer_loadrun01")
        self.assertEqual(contract.qualified_function_name, "public.clouddsp_six_stem_observe_loadrun01")
        self.assertEqual(
            contract.role_attributes,
            (
                "LOGIN",
                "NOSUPERUSER",
                "NOCREATEDB",
                "NOCREATEROLE",
                "NOREPLICATION",
                "NOINHERIT",
                "CONNECTION LIMIT 1",
            ),
        )
        self.assertEqual(len(contract.permitted_grants), 3)
        for required in (
            'CREATE FUNCTION "public"."clouddsp_six_stem_observe_loadrun01"()',
            "RETURNS TABLE",
            "SECURITY DEFINER",
            "SET search_path = pg_catalog, public",
            "FROM public.jobs AS j",
            "FROM public.processing_tasks AS t",
            "FROM public.outbox_events AS e",
            "artifact_evidence JSONB",
            "jsonb_each(stems)",
            "input_object_key",
            "source_filename",
            'GRANT CONNECT ON DATABASE "clouddsp_job_api" TO "clouddsp_six_stem_observer_loadrun01"',
            'GRANT USAGE ON SCHEMA "public" TO "clouddsp_six_stem_observer_loadrun01"',
            "REVOKE ALL ON FUNCTION",
            'GRANT EXECUTE ON FUNCTION "public"."clouddsp_six_stem_observe_loadrun01"() TO "clouddsp_six_stem_observer_loadrun01"',
        ):
            self.assertIn(required, contract.definition_sql)
        for forbidden in (
            "SELECT *",
            "payload",
            "error_message",
            "INSERT INTO",
            "UPDATE public.",
            "DELETE FROM",
            "DROP ",
            "CREATE ROLE",
            "PASSWORD",
        ):
            self.assertNotIn(forbidden, contract.definition_sql)
        self.assertEqual(
            contract.permitted_grants,
            (
                "CONNECT on the CloudDSP application database",
                "USAGE on schema public",
                "EXECUTE on function public.clouddsp_six_stem_observe_loadrun01()",
            ),
        )
        self.assertNotIn(_SUBJECT, repr(contract))
        for job_id in _JOB_IDS:
            self.assertIn(job_id, contract.definition_sql)
            self.assertNotIn(job_id, repr(contract))

    def test_contract_refuses_scope_that_is_not_the_prior_exact_object_contract(self) -> None:
        """A future role cannot be paired with another Job's prefix or source object."""

        capability = derive_verified_load_observer_capability_contract(proof=proof())
        widened_scope = replace(capability.object_scopes[0], stems_prefix="stems/")
        widened_capability = replace(
            capability, object_scopes=(widened_scope, *capability.object_scopes[1:])
        )

        with self.assertRaisesRegex(PostgreSQLObserverContractError, "object scope"):
            build_postgresql_observer_access_contract(capability=widened_capability)

    def test_contract_refuses_noncanonical_or_duplicate_job_identifiers(self) -> None:
        """The fixed three-ID function cannot be widened through a hand-built dataclass."""

        capability = derive_verified_load_observer_capability_contract(proof=proof())
        duplicate_capability = replace(
            capability,
            job_ids=(capability.job_ids[0], capability.job_ids[0], capability.job_ids[2]),
        )

        with self.assertRaisesRegex(PostgreSQLObserverContractError, "duplicated"):
            build_postgresql_observer_access_contract(capability=duplicate_capability)

    def test_contract_refuses_non_capability_input(self) -> None:
        """No UUID array, handoff marker, browser request, or raw dictionary is accepted."""

        with self.assertRaisesRegex(PostgreSQLObserverContractError, "not verified capability"):
            build_postgresql_observer_access_contract(capability=object())  # type: ignore[arg-type]


if __name__ == "__main__":  # pragma: no cover - direct local teaching command.
    unittest.main()

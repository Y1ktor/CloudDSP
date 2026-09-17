# ADTOF exhausted-lease recovery smoke test

This focused live smoke test proves the terminal recovery branch for an ADTOF
task whose third active lease has expired. It does not exercise browser upload,
MinIO, RabbitMQ publication, the dispatcher, ADTOF inference, MIDI output, or
Job-level aggregation; those belong to their own smoke tests.

The Job creates exactly one synthetic Job/outbox/task coordinate. The outbox
event is already `published`, so the dispatcher cannot publish it again. The
task is `leased`, at `attempt_count = 3`, and has a past lease expiry. The
deployed ADTOF worker must change that task to `failed`, clear both lease
columns, set `completed_at`, and write only
`lease_expired_attempts_exhausted` as its safe error code. The outer Job status
must remain `midi_processing`, with no MIDI or tempo result.

On success the test deletes only its exact synthetic Job after rechecking every
expected state. PostgreSQL's foreign keys cascade its matching event and task.
If seeding, polling, or validation fails, it intentionally leaves the fixed
rows for inspection; do not rerun until those diagnosed rows have been removed
with an explicitly reviewed, exact-coordinate cleanup operation.

The manifest is prepared but not applied by this task.

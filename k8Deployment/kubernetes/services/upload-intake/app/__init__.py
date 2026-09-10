"""Pure building blocks for CloudDSP's local upload-intake consumer.

This package intentionally starts with no AMQP client, MinIO SDK, PostgreSQL
driver, HTTP server, or Kubernetes API client. The first parser turns one
untrusted MinIO notification into narrow in-memory candidates. Its companion
database-transition module accepts a caller-owned cursor and encodes only the
restricted, idempotent PostgreSQL queries. The private object-storage module
accepts an injected HeadObject client and compares returned metadata without
downloading media. The message handler composes those boundaries through
injected read/write transaction contexts, but still has no queue client or
acknowledgement side effect. The concrete Psycopg adapter now supplies those
transaction contexts through one restricted PostgreSQL login. The AMQP adapter
owns one manual-ack delivery at a time, and the consumer runtime now composes
the adapters into a reconnecting process loop. A later task still needs the
bounded retry/DLQ publish policy before this process becomes a Deployment.
"""

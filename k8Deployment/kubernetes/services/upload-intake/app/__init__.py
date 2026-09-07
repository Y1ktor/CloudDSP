"""Pure building blocks for CloudDSP's local upload-intake consumer.

This package intentionally starts with no AMQP client, MinIO SDK, PostgreSQL
driver, HTTP server, or Kubernetes API client.  The first module turns one
untrusted MinIO notification into narrow in-memory candidates.  Later tasks
will compose that reviewed parser with separate queue, storage, and durable
state boundaries.
"""

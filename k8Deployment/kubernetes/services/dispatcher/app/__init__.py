"""Kubernetes-local PostgreSQL-outbox dispatcher package.

Keeping these modules in a regular Python package makes the later container
entrypoint explicit: ``python -m app.dispatcher_runtime`` starts only the
dispatcher supervisor.  The package contains no cluster credentials, HTTP
listener, Kubernetes client, Demucs code, or import-time network operation.
Those boundaries keep its least-privilege publisher role small and testable.
"""

"""Kubernetes-local Basic Pitch worker boundaries.

Modules in this package remain independent of the preserved cloud Lambda
handler. They will compose private MinIO, PostgreSQL, RabbitMQ, and Basic Pitch
adapters explicitly as later small tasks introduce each responsibility.
"""

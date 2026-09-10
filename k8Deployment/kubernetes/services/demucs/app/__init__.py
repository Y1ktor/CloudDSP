"""Kubernetes-local Demucs worker modules.

This package is independent from the preserved cloud handlers. Each module owns
one bounded responsibility, allowing unit tests to exercise worker correctness
without a Kubernetes cluster, RabbitMQ socket, MinIO object, or GPU.
"""

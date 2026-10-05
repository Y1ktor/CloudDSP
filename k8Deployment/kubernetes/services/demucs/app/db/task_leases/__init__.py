"""Internal Demucs task adapters grouped by durable database responsibility.

Import public worker contracts and operations through ``app.db.task_lease``.
Submodules depend directly on shared contracts/validation, keeping the public
facade out of the internal dependency graph and avoiding circular imports.
"""

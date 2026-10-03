"""Path adapter for optional host-side Python maintenance commands.

Like paths.rb and paths.sh, this resolves only committed project directories.
It never reads credential files or contacts a cluster.
"""
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
KUBERNETES_ROOT = SCRIPT_ROOT.parent
PROJECT_ROOT = KUBERNETES_ROOT.parent

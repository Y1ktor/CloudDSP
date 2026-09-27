#!/usr/bin/env bash
# Protect the live one-Pod PostgreSQL cluster before a stateful Helm takeover.
# pg_dumpall includes database contents, roles, and password hashes; the file
# must remain in the ignored, owner-only local directory and never enter Git.
# A separate Docker container with no network or published port rehearses a
# complete SQL restore. It never mounts or connects to the live Kubernetes PVC.
set -euo pipefail

if [[ $# -ne 0 ]]; then
  printf 'Usage: %s\n' "$0" >&2
  exit 2
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
backup_dir="$repo_root/k8Deployment/.local/backups"
image='docker.io/library/postgres@sha256:051f7b7b3abdd564d5d1bd1e8c4b9c1b6e77087d1dd22020ede611c096a272e0'
context='k3d-clouddsp-local'
namespace='clouddsp-data'
pod='clouddsp-postgresql-0'
pvc='postgres-data-clouddsp-postgresql-0'
restore_container=''

cleanup() {
  if [[ -n "$restore_container" ]]; then
    docker rm -f "$restore_container" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

if [[ "$(kubectl --context "$context" -n "$namespace" get statefulset/clouddsp-postgresql -o jsonpath='{.status.readyReplicas}')" != '1' ]]; then
  printf 'PostgreSQL backup stopped: StatefulSet is not Ready.\n' >&2
  exit 1
fi
if [[ "$(kubectl --context "$context" -n "$namespace" get "pvc/$pvc" -o jsonpath='{.status.phase}')" != 'Bound' ]]; then
  printf 'PostgreSQL backup stopped: expected PVC is not Bound.\n' >&2
  exit 1
fi
docker info >/dev/null 2>&1 || {
  printf 'PostgreSQL backup stopped: Docker is unavailable for restore rehearsal.\n' >&2
  exit 1
}

# Restrict every new artifact to the local user. The ignored directory is a
# convenience, not off-machine disaster protection; copy/encrypt it separately
# under a reviewed backup policy before relying on it for recovery.
umask 077
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
backup_file="$backup_dir/postgresql-$(date -u +%Y%m%dT%H%M%SZ)-$$.sql"
restore_log="${backup_file%.sql}.restore.log"
if ! kubectl --context "$context" -n "$namespace" exec "pod/$pod" -- \
  sh -ec 'exec pg_dumpall --clean --if-exists --username="$POSTGRES_USER"' \
  > "$backup_file" 2>/dev/null; then
  rm -f "$backup_file"
  printf 'PostgreSQL backup stopped: pg_dumpall failed.\n' >&2
  exit 1
fi
if [[ ! -s "$backup_file" ]]; then
  rm -f "$backup_file"
  printf 'PostgreSQL backup stopped: dump file is empty.\n' >&2
  exit 1
fi
chmod 600 "$backup_file"

# A different bootstrap superuser avoids conflicts with the live cluster's
# restored postgres role. The disposable instance is unreachable over Docker
# networking; trust authentication applies only inside this throwaway sandbox.
restore_container="clouddsp-pg-restore-$(date -u +%H%M%S)-$$"
if ! docker run --rm --detach --network none --name "$restore_container" \
  --env POSTGRES_USER=restore_admin --env POSTGRES_HOST_AUTH_METHOD=trust \
  "$image" >/dev/null 2>&1; then
  printf 'PostgreSQL restore rehearsal stopped: isolated container did not start. Backup retained.\n' >&2
  exit 1
fi

ready='false'
for _ in $(seq 1 60); do
  if docker exec "$restore_container" pg_isready -U restore_admin -d template1 >/dev/null 2>&1; then
    ready='true'
    break
  fi
  sleep 1
done
if [[ "$ready" != 'true' ]]; then
  printf 'PostgreSQL restore rehearsal stopped: isolated server did not become Ready. Backup retained.\n' >&2
  exit 1
fi

# pg_dumpall --clean drops and recreates the source cluster's ordinary
# databases. Connect restore psql to a scratch control database that is absent
# from the source dump, so the script can safely drop the restored `postgres`
# database without dropping the connection it is using.
if ! docker exec "$restore_container" createdb --username=restore_admin restore_control >/dev/null 2>&1; then
  printf 'PostgreSQL restore rehearsal stopped: scratch control database was not created. Backup retained.\n' >&2
  exit 1
fi
# The image initializes a database named for POSTGRES_USER. That scratch
# `restore_admin` database is not in the live dump and would inflate the
# restored database count, so remove it before replaying the source backup.
if ! docker exec "$restore_container" dropdb --username=restore_admin restore_admin >/dev/null 2>&1; then
  printf 'PostgreSQL restore rehearsal stopped: scratch user database was not removed. Backup retained.\n' >&2
  exit 1
fi

# The dump can contain credential hashes and owner data. Keep all psql output
# in a private file and show only a generic status on failure. ON_ERROR_STOP
# makes any SQL restore error fail the adoption gate instead of being skipped.
if ! docker exec --interactive "$restore_container" \
  psql --no-psqlrc --set=ON_ERROR_STOP=1 --username=restore_admin --dbname=restore_control \
  < "$backup_file" > "$restore_log" 2>&1; then
  chmod 600 "$restore_log"
  printf 'PostgreSQL restore rehearsal failed; private diagnostics are in the ignored backup directory. Backup retained.\n' >&2
  exit 1
fi
rm -f "$restore_log"

# Compare only counts, never database/role names or row data in command output.
# The one extra restore_admin login exists only in the isolated scratch cluster.
live_databases="$(kubectl --context "$context" -n "$namespace" exec "pod/$pod" -- \
  sh -ec 'exec psql --no-psqlrc -Atq -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT count(*) FROM pg_database WHERE datistemplate = false"')"
restored_databases="$(docker exec "$restore_container" \
  psql --no-psqlrc -Atq -U restore_admin -d restore_control -c \
  "SELECT count(*) FROM pg_database WHERE datistemplate = false AND datname <> 'restore_control'")"
live_logins="$(kubectl --context "$context" -n "$namespace" exec "pod/$pod" -- \
  sh -ec 'exec psql --no-psqlrc -Atq -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT count(*) FROM pg_roles WHERE rolcanlogin"')"
restored_logins="$(docker exec "$restore_container" \
  psql --no-psqlrc -Atq -U restore_admin -d restore_control -c \
  "SELECT count(*) FROM pg_roles WHERE rolcanlogin AND rolname <> 'restore_admin'")"
if [[ ! "$live_databases" =~ ^[0-9]+$ || ! "$restored_databases" =~ ^[0-9]+$ || \
      ! "$live_logins" =~ ^[0-9]+$ || ! "$restored_logins" =~ ^[0-9]+$ || \
      "$live_databases" != "$restored_databases" || "$live_logins" != "$restored_logins" ]]; then
  printf 'PostgreSQL restore rehearsal failed: counts differ (databases %s/%s; logins %s/%s). Backup retained.\n' \
    "$live_databases" "$restored_databases" "$live_logins" "$restored_logins" >&2
  exit 1
fi

printf 'PostgreSQL backup and isolated restore passed: %s databases, %s login roles.\n' \
  "$live_databases" "$live_logins"
printf 'Private backup retained at %s (%s bytes, mode 600).\n' \
  "$backup_file" "$(wc -c < "$backup_file" | tr -d ' ')"

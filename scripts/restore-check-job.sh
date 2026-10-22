#!/bin/sh
# Runs inside the pgvector image (the weekly restore-check CronJob, and the restore drill): proves the newest nightly backup restores into a scratch
# database, from the file, with the documented flags. A backup that has never been restored is a hope, not a backup.
# BACKUP_DIR (default /backups); MAX_AGE_SECONDS (default 172800: the newest dump must be less than two days old); SCRATCH_PORT (default 5432:
# the scratch server's port; the drill shares a pod with other databases, so it picks another).
set -eu
dir="${BACKUP_DIR:-/backups}"
file="$(ls -t "$dir"/fabulist-*.dump 2>/dev/null | head -1)"
[ -n "$file" ] || { echo "FAIL: no backup in $dir" >&2; exit 1; }
age=$(( $(date +%s) - $(stat -c %Y "$file") ))
[ "$age" -lt "${MAX_AGE_SECONDS:-172800}" ] || { echo "FAIL: the newest backup ($file) is $((age / 3600)) hours old" >&2; exit 1; }
echo "restoring $file ($(du -h "$file" | cut -f1)) into a scratch database"
export POSTGRES_PASSWORD=scratch PGPASSWORD=scratch PGPORT="${SCRATCH_PORT:-5432}"
docker-entrypoint.sh postgres -c listen_addresses=127.0.0.1 -c port="$PGPORT" -c fsync=off > /tmp/scratch-postgres.log 2>&1 &
for _ in $(seq 1 90); do pg_isready -q -h 127.0.0.1 -U postgres && break; sleep 1; done
pg_isready -q -h 127.0.0.1 -U postgres || { echo "FAIL: the scratch database did not start" >&2; tail -5 /tmp/scratch-postgres.log >&2; exit 1; }
createdb -h 127.0.0.1 -U postgres restorecheck
# --no-owner --no-privileges: the scratch server has none of the production roles (fabulist, fabulist_app); everything else must restore without a single error
pg_restore -h 127.0.0.1 -U postgres -d restorecheck --no-owner --no-privileges --exit-on-error "$file"
tables="$(psql -h 127.0.0.1 -U postgres -d restorecheck -Atc "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")"
[ "$tables" -ge 15 ] || { echo "FAIL: only $tables tables after the restore" >&2; exit 1; }
# the data is really there: every table can be read, and the audit trail's append-only protection came back with it
psql -h 127.0.0.1 -U postgres -d restorecheck -Atc "SELECT (SELECT count(*) FROM projects), (SELECT count(*) FROM requirements), (SELECT count(*) FROM stories), (SELECT count(*) FROM history), (SELECT count(*) FROM admin_audit), (SELECT count(*) FROM settings)" > /tmp/counts
triggers="$(psql -h 127.0.0.1 -U postgres -d restorecheck -Atc "SELECT count(*) FROM pg_trigger WHERE tgname LIKE '%append_only'")"
[ "$triggers" -ge 2 ] || { echo "FAIL: the append-only triggers did not come back ($triggers)" >&2; exit 1; }
echo "restore check ok: $file; $tables tables; projects/requirements/stories/history/admin_audit/settings = $(tr '|' ' ' < /tmp/counts)"

#!/bin/sh
# Backup retention, run by the nightly backup CronJob after it has written a new dump (and tested in the restore drill):
# every dump of the last 7 days, and the Sunday dump of each of the last 5 weeks (35 days). Older ones are deleted.
# BACKUP_DIR (default /backups); the age comes from the file's modification time, the weekday from the date in its name (fabulist-YYYYMMDD-HHMMSS.dump).
set -eu
dir="${BACKUP_DIR:-/backups}"
now="$(date +%s)"
for f in "$dir"/fabulist-*.dump; do
  [ -e "$f" ] || continue
  name="${f##*/}"; stamp="${name#fabulist-}"; day="${stamp%%-*}"
  age_days=$(( (now - $(stat -c %Y "$f")) / 86400 ))
  weekday="$(date -u -d "$day" +%u 2>/dev/null || echo 0)"   # 7 = Sunday; 0: a name we cannot read is treated as an ordinary dump
  if [ "$age_days" -gt 35 ] || { [ "$age_days" -gt 7 ] && [ "$weekday" != 7 ]; }; then
    echo "pruning $name ($age_days days old)"
    rm -f "$f"
  fi
done
ls -lh "$dir"

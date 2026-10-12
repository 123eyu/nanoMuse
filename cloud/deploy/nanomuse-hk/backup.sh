#!/bin/sh
# Daily copy of the relay's SQLite database, taken with SQLite's own backup
# (consistent under WAL, no need to stop the container), gzipped, kept for 30
# days. Run by nanomuse-relay-backup.timer; harmless to run by hand.
#
#   RELAY_DIR     where docker-compose.yml lives (default /opt/nanomuse/relay)
#   BACKUP_DIR    where copies go (default /opt/nanomuse/backups)
#   KEEP_DAYS     how many days of copies to keep (default 30)
#   BACKUP_OWNER  who owns the copies, as user:group (default: the owner of RELAY_DIR,
#                 the deploying user that deploy.sh gave the directory to)
#
# The copies are only useful together with CLOUD_SECRET from .env (the
# hashes and the encrypted identifiers are keyed by it), so .env is copied
# alongside, once, and again whenever it changes, with the same 0600 mode.
#
# The timer runs this as root, and the database copy comes out of the container
# owned by uid 10001, so without the chown below every file under BACKUP_DIR
# was readable by root alone and `scp nanomuse-hk:/opt/nanomuse/backups/...`
# as the deploying user failed. The copies are handed to BACKUP_OWNER, mode
# 0600, so that user can read, copy off and restore them without sudo.
set -eu

relay=${RELAY_DIR:-/opt/nanomuse/relay}
out=${BACKUP_DIR:-/opt/nanomuse/backups}
keep=${KEEP_DAYS:-30}
owner=${BACKUP_OWNER:-$(stat -c '%u:%g' "$relay")}
stamp=$(date -u +%Y%m%d-%H%M%S)

umask 077
mkdir -p "$out"
chown "$owner" "$out"

# sqlite3 inside the relay image (python:3.12-slim has the module, not the CLI):
# python's sqlite3 backup API writes a consistent snapshot to a new file.
docker exec -i nanomuse-relay python - "$stamp" <<'EOF'
import sqlite3, sys
src = sqlite3.connect("/srv/nanomuse-cloud/data/cloud.db")
dst = sqlite3.connect(f"/srv/nanomuse-cloud/data/.backup-{sys.argv[1]}.db")
with dst:
    src.backup(dst)
dst.close(); src.close()
EOF
mv "$relay/data/.backup-$stamp.db" "$out/cloud-$stamp.db"
gzip -f "$out/cloud-$stamp.db"
chown "$owner" "$out/cloud-$stamp.db.gz"
chmod 600 "$out/cloud-$stamp.db.gz"

if [ -f "$relay/.env" ] && ! cmp -s "$relay/.env" "$out/env.latest" 2>/dev/null; then
	cp "$relay/.env" "$out/env-$stamp"
	cp "$relay/.env" "$out/env.latest"
	chown "$owner" "$out/env-$stamp" "$out/env.latest"
	chmod 600 "$out/env-$stamp" "$out/env.latest"
fi

find "$out" -name 'cloud-*.db.gz' -mtime +"$keep" -delete
echo "backup: $out/cloud-$stamp.db.gz ($(du -h "$out/cloud-$stamp.db.gz" | cut -f1))"

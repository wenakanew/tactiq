#!/bin/sh
set -eu
test -f /opt/tactiq/database_vm_backup.py
chmod 700 /opt/tactiq/database_vm_backup.py
cat > /etc/systemd/system/tactiq-database-backup.service <<'UNIT'
[Unit]
Description=Tactiq private PostgreSQL logical backup
After=network-online.target postgresql@17-tactiq.service
Wants=network-online.target
RequiresMountsFor=/srv/postgresql
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /opt/tactiq/database_vm_backup.py
TimeoutStartSec=300
UMask=0077
PrivateTmp=true
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
UNIT
cat > /etc/systemd/system/tactiq-database-backup.timer <<'UNIT'
[Unit]
Description=Daily Tactiq private database backup
[Timer]
OnCalendar=*-*-* 03:15:00 UTC
RandomizedDelaySec=300
Persistent=true
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now tactiq-database-backup.timer
/usr/bin/python3 /opt/tactiq/database_vm_backup.py --verify-restore
systemctl is-active tactiq-database-backup.timer

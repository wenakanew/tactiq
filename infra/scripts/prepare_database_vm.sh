#!/bin/sh
set -eu
export DEBIAN_FRONTEND=noninteractive
if [ ! -f /swapfile ]; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
    swapon /swapfile
    printf '/swapfile none swap sw 0 0\n' >> /etc/fstab
fi
apt-get update -qq
apt-get install -y -qq postgresql-17 python3-psycopg2 python3-requests openssl unattended-upgrades
systemctl stop postgresql@17-main
printf 'manual\n' > /etc/postgresql/17/main/start.conf
device=/dev/disk/azure/scsi1/lun0
if [ ! -b "$device" ]; then
    echo 'Expected dedicated PostgreSQL data disk is absent' >&2
    exit 1
fi
if ! blkid "$device" >/dev/null 2>&1; then
    mkfs.ext4 -q "$device"
fi
uuid=$(blkid -s UUID -o value "$device")
mkdir -p /srv/postgresql
if ! grep -q "UUID=$uuid " /etc/fstab; then
    printf 'UUID=%s /srv/postgresql ext4 defaults,nofail 0 2\n' "$uuid" >> /etc/fstab
fi
mountpoint -q /srv/postgresql || mount /srv/postgresql
if [ ! -f /etc/postgresql/17/tactiq/postgresql.conf ]; then
    pg_createcluster --datadir /srv/postgresql/17/tactiq 17 tactiq
fi
mkdir -p /etc/systemd/system/postgresql@17-tactiq.service.d
printf '[Unit]\nRequiresMountsFor=/srv/postgresql\n' > /etc/systemd/system/postgresql@17-tactiq.service.d/storage.conf
systemctl daemon-reload
mkdir -p /usr/local/lib/tactiq
systemctl enable postgresql@17-tactiq
systemctl enable apt-daily.timer apt-daily-upgrade.timer
echo 'Dedicated durable database volume and PostgreSQL 17 cluster prepared.'

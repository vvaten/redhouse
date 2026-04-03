# RedHouse Backup & Restoration Guide

## Overview

Two independent backup jobs run daily:

| Job | Runs on | Schedule | What | Size | Duration |
|-----|---------|----------|------|------|----------|
| Pi backup | Raspberry Pi (systemd timer) | 03:00 UTC | .env, pump_state.json, systemd units | ~40 KB | <1s |
| NAS backup | Asustor NAS (crontab) | 03:30 UTC | InfluxDB (native backup) + Grafana (API export) | ~300 MB | ~2.5 min |

Backups are stored on the NAS at `/share/Backups/redhouse/`.
Retention: 30 daily + 12 weekly snapshots (~114 days).

## Backup Locations

```
/share/Backups/redhouse/
    pi/                                 # Pi file backups (rsync from Pi)
        YYYY-MM-DD_HHMMSS/
            .env
            data/pump_state.json
            systemd/redhouse-*.service
            systemd/redhouse-*.timer
            backup_manifest.json
        latest -> YYYY-MM-DD_HHMMSS    # symlink to newest

    nas/                                # NAS-local backups
        YYYY-MM-DD_HHMMSS/
            influxdb/                   # native influx backup files
            grafana/
                dashboards/*.json
                alert_rules.json
                contact_points.json
                notification_policies.json
            backup_manifest.json
        latest -> YYYY-MM-DD_HHMMSS
        .operator_token                 # InfluxDB operator token (chmod 600, root)
        .grafana_api_key                # Grafana API key (chmod 600, root)
        backup.log                      # cron output log

    scripts/                            # NAS backup scripts (copied from repo)
        run_backup_nas.sh
        backup_influxdb.sh
        backup_grafana.sh
        restore_influxdb.sh
        restore_grafana.sh
        test_restore_influxdb.sh
        test_restore_grafana.sh
```

## Setup (One-Time)

### Pi Side

1. Add backup config to `/opt/redhouse/.env`:
   ```
   BACKUP_NAS_HOST=192.168.1.164
   BACKUP_NAS_USER=redhouse-backup
   BACKUP_NAS_SSH_KEY=/home/pi/.ssh/redhouse_backup_key
   BACKUP_NAS_PATH=/share/Backups/redhouse/pi
   BACKUP_NAS_LOCAL_PATH=/share/Backups/redhouse/nas
   ```

2. SSH key setup (if not already done):
   ```bash
   ssh-keygen -t ed25519 -f /home/pi/.ssh/redhouse_backup_key -N "" -C "redhouse-backup@pi"
   ssh-copy-id -i /home/pi/.ssh/redhouse_backup_key.pub redhouse-backup@192.168.1.164
   ```

3. Deploy and enable timer:
   ```bash
   cd /opt/redhouse && git pull
   sudo cp deployment/systemd/redhouse-backup.service /etc/systemd/system/
   sudo cp deployment/systemd/redhouse-backup.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now redhouse-backup.timer
   ```

### NAS Side

1. Create NAS user `redhouse-backup` via Asustor admin panel
2. Add to `administrators` group (required for SSH access on Asustor):
   ```bash
   addgroup redhouse-backup administrators
   ```

3. Create backup directories:
   ```bash
   mkdir -p /share/Backups/redhouse/pi
   mkdir -p /share/Backups/redhouse/nas
   mkdir -p /share/Backups/redhouse/scripts
   ```

4. Add `/backups` volume mount to InfluxDB container in docker-compose.yml:
   ```yaml
   influxdb:
     volumes:
       - /volume1/home/vvaten/influxdb2:/var/lib/influxdb2:rw
       - /share/Backups/redhouse/nas:/backups:rw
   ```
   Then: `cd /volume1/home/vvaten && docker compose up -d influxdb`

5. Create operator token (requires stopping InfluxDB):
   ```bash
   docker stop influxdb2
   docker run --rm -v /volume1/home/vvaten/influxdb2:/var/lib/influxdb2 influxdb:latest \
       influxd recovery auth create-operator --org area51 --username pi \
       --bolt-path /var/lib/influxdb2/influxd.bolt
   docker start influxdb2
   ```
   Save the token:
   ```bash
   echo 'YOUR_OPERATOR_TOKEN' > /share/Backups/redhouse/nas/.operator_token
   chown root:root /share/Backups/redhouse/nas/.operator_token
   chmod 600 /share/Backups/redhouse/nas/.operator_token
   ```

6. Create Grafana API key via Grafana UI (http://192.168.1.164:3000 > Administration > Service Accounts > Add token):
   ```bash
   echo 'YOUR_GRAFANA_API_KEY' > /share/Backups/redhouse/nas/.grafana_api_key
   chown root:root /share/Backups/redhouse/nas/.grafana_api_key
   chmod 600 /share/Backups/redhouse/nas/.grafana_api_key
   ```

7. Copy scripts from Pi and schedule:
   ```bash
   # From Pi:
   rsync -avz -e "ssh -i /home/pi/.ssh/redhouse_backup_key" \
       /opt/redhouse/scripts/backup/nas/ \
       redhouse-backup@192.168.1.164:/share/Backups/redhouse/scripts/

   # On NAS, add to root crontab:
   sudo crontab -l > /tmp/crontab_bak
   echo '30 3 * * * sh /share/Backups/redhouse/scripts/run_backup_nas.sh >> /share/Backups/redhouse/nas/backup.log 2>&1' >> /tmp/crontab_bak
   sudo crontab /tmp/crontab_bak
   ```

## Manual Operations

### Run Pi backup manually
```bash
# On Pi
source venv/bin/activate
python -u scripts/backup/run_backup_pi.py          # real backup
python -u scripts/backup/run_backup_pi.py --dry-run # test only
```

### Run NAS backup manually
```bash
# On NAS (as root)
sudo sh /share/Backups/redhouse/scripts/run_backup_nas.sh          # real backup
sudo sh /share/Backups/redhouse/scripts/run_backup_nas.sh --dry-run # test only
```

### Check backup status
```bash
# On NAS: check latest manifests
cat /share/Backups/redhouse/pi/latest/backup_manifest.json
cat /share/Backups/redhouse/nas/latest/backup_manifest.json

# On NAS: check backup log
tail -50 /share/Backups/redhouse/nas/backup.log

# On Pi: health check reports backup freshness
source venv/bin/activate && python -u run_health_check.py
```

### Verify backup integrity
```bash
# Pi backup: check SHA-256 hashes
source venv/bin/activate
python -u scripts/backup/verify_backup_pi.py /path/to/backup/ --verbose

# NAS backup: run restore test (non-destructive)
sudo sh /share/Backups/redhouse/scripts/test_restore_influxdb.sh /share/Backups/redhouse/nas/latest
sudo sh /share/Backups/redhouse/scripts/test_restore_grafana.sh /share/Backups/redhouse/nas/latest
```

## Restoration Runbook

### Scenario A: Pi SD Card Failure

The Pi has no unique InfluxDB/Grafana data -- only config files.

1. Flash new Raspbian on SD card
2. Clone repo:
   ```bash
   cd /opt && sudo git clone <repo-url> redhouse
   sudo chown -R pi:pi /opt/redhouse
   ```
3. Set up SSH key for NAS access:
   ```bash
   ssh-keygen -t ed25519 -f /home/pi/.ssh/redhouse_backup_key -N "" -C "redhouse-backup@pi"
   ssh-copy-id -i /home/pi/.ssh/redhouse_backup_key.pub redhouse-backup@192.168.1.164
   ```
4. Pull latest backup from NAS:
   ```bash
   rsync -avz -e "ssh -i /home/pi/.ssh/redhouse_backup_key" \
       redhouse-backup@192.168.1.164:/share/Backups/redhouse/pi/latest/ \
       /tmp/redhouse-restore/
   ```
5. Restore files:
   ```bash
   cp /tmp/redhouse-restore/.env /opt/redhouse/.env
   mkdir -p /opt/redhouse/data
   cp /tmp/redhouse-restore/data/pump_state.json /opt/redhouse/data/
   sudo cp /tmp/redhouse-restore/systemd/* /etc/systemd/system/
   sudo systemctl daemon-reload
   ```
6. Install dependencies:
   ```bash
   cd /opt/redhouse
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
7. Enable services:
   ```bash
   sudo systemctl enable --now redhouse-*.timer
   ```
8. Verify:
   ```bash
   source venv/bin/activate
   python -u run_health_check.py
   ```

### Scenario B: InfluxDB Data Loss / Corruption

InfluxDB backup and restore both happen on the NAS.

1. Ensure InfluxDB container is running:
   ```bash
   docker ps | grep influxdb2
   ```
2. Find latest backup:
   ```bash
   ls -lt /share/Backups/redhouse/nas/
   ```
3. Restore (interactive, asks for confirmation):
   ```bash
   sudo sh /share/Backups/redhouse/scripts/restore_influxdb.sh \
       /share/Backups/redhouse/nas/latest/influxdb
   ```
4. Verify in InfluxDB UI or via Flux queries
5. If needed, re-run aggregation backfill on Pi:
   ```bash
   python -u deployment/backfill_aggregation.py --days 7 --skip-5min
   ```

### Scenario C: Grafana Loss

1. Ensure Grafana container is running
2. Create new API key if needed (Administration > Service Accounts)
3. Restore dashboards:
   ```bash
   sudo sh /share/Backups/redhouse/scripts/restore_grafana.sh \
       /share/Backups/redhouse/nas/latest/grafana
   ```

### Scenario D: Full NAS Failure

Worst case -- NAS hardware failure.

1. Replace/repair NAS hardware
2. Reinstall Docker, create InfluxDB + Grafana containers
3. If NAS backup disk survived:
   - Restore InfluxDB (Scenario B)
   - Restore Grafana (Scenario C)
4. If backup disk lost:
   - Data is gone; Pi will start collecting fresh data once NAS is reachable
   - Recreate `.env` from `.env.example` and git history
5. Re-run NAS setup steps from "Setup (One-Time)" section above

**Mitigation**: consider periodic off-site copy of NAS backups (USB drive, cloud).

## Monitoring

The Pi health check (runs every 15 minutes) monitors backup freshness:
- **Warning**: backup older than 26 hours (missed one cycle)
- **Failure**: backup older than 48 hours
- Alerts sent via email (Resend API)

Check configured via `BACKUP_NAS_HOST`, `BACKUP_NAS_PATH`, `BACKUP_NAS_LOCAL_PATH` in `.env`.

## Tokens

| Token | Purpose | Location | Created via |
|-------|---------|----------|-------------|
| InfluxDB operator token | `influx backup/restore` | `/share/Backups/redhouse/nas/.operator_token` | `influxd recovery auth create-operator` |
| Grafana API key | Dashboard export/import | `/share/Backups/redhouse/nas/.grafana_api_key` | Grafana UI > Service Accounts |
| SSH key (Pi) | rsync Pi files to NAS | `/home/pi/.ssh/redhouse_backup_key` | `ssh-keygen` |

## Asustor NAS Notes

- SSH only allows `administrators` group members (Asustor limitation)
- Shell is BusyBox `ash`, not `bash` -- scripts use `#!/bin/sh`
- `sudo` resets PATH -- scripts prepend `/usr/builtin/bin` for `curl`, `docker`
- Operator token cannot be created via UI; requires `influxd recovery` with container stopped
- NAS backup scheduled via `sudo crontab` (no scheduled tasks UI for custom scripts)

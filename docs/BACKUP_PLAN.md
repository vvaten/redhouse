# RedHouse Backup & Restoration System -- Implementation Plan

## Context

The RedHouse home automation system has no automated backup strategy. Critical data lives in two places:
- **Raspberry Pi**: `.env` (credentials), `config.yaml`, `pump_state.json` -- loss means manual recreation
- **NAS (InfluxDB)**: 11+ production buckets with years of time-series data -- loss means permanent data loss
- **NAS (Grafana)**: Dashboards and alert rules

The goal is a fully automated backup system with a tested restoration process, so recovery from any failure scenario (Pi SD card death, InfluxDB corruption, NAS failure) is documented and exercised.

## Architecture

Two independent backup jobs, each running where the data lives:

```
Pi (03:00 daily, systemd timer)
    run_backup_pi.py --> /mnt/nas-backup/redhouse/pi/
                             +-- YYYY-MM-DD_HHMMSS/
                             |     +-- .env, config.yaml, pump_state.json
                             |     +-- systemd/  (redhouse-*.service, *.timer)
                             |     +-- backup_manifest.json
                             +-- latest -> symlink

NAS (03:30 daily, Asustor scheduled job)
    run_backup_nas.sh --> /share/Backups/redhouse/
                             +-- YYYY-MM-DD_HHMMSS/
                             |     +-- influxdb/  (native InfluxDB backup)
                             |     +-- grafana/   (dashboard JSON + alert rules)
                             |     +-- backup_manifest.json
                             +-- latest -> symlink
```

**Key principle**: each machine backs up only its own data. No cross-network
round-trips.

## Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Split Pi / NAS | Two independent jobs | Avoids NAS->Pi->NAS round-trip for InfluxDB; each machine backs up its own data |
| InfluxDB backup method | `influx backup` (native) via Docker exec | Fast, consistent snapshot; no query timeouts; supports full restore with `influx restore` |
| Grafana backup method | REST API JSON export from NAS | Data stays local; same pattern as `clone_grafana_dashboard_to_staging.py` |
| Pi backup target | NAS via NFS/CIFS mount | Pi SD card is most failure-prone; backups must survive Pi death |
| NAS backup target | Local NAS directory | Data already on NAS; no network needed |
| Backup schedule | Pi at 03:00, NAS at 03:30 | Low-activity window; staggered to avoid overlap |
| InfluxDB full vs incremental | Weekly full + daily incremental (`--since`) | `influx backup --since` supports incremental; balances speed vs coverage |
| Backup retention | 30 daily + 12 weekly | ~3 months of recovery points |
| NAS scheduler | Asustor admin panel scheduled job | Native NAS scheduling; no cron hacks needed |

## Backup Size Estimates

### Pi files (trivial)

| File | Size |
|------|------|
| `.env` | ~2 KB |
| `config/config.yaml` | ~5 KB |
| `data/pump_state.json` | ~1 KB |
| Systemd units (26 files) | ~30 KB |
| **Total per backup** | **~40 KB** |

### Grafana (small)

| Item | Size |
|------|------|
| Dashboards (5-10 JSON files) | ~500 KB - 2 MB |
| Alert rules + contact points | ~50 KB |
| **Total per backup** | **~2 MB** |

### InfluxDB (dominant)

Daily data generation rates (for estimating incremental size):

| Bucket | Collection Rate | Daily Points | Est. Daily Raw Size |
|--------|----------------|-------------|-------------------|
| `temperatures` | 1/min, ~15 sensors | 1,440 | ~6.5 MB |
| `shelly_em3_emeters_raw` | 1/min, 3 phases | 1,440 | ~4.3 MB |
| `checkwatt_full_data` | 1/5min, 6 fields | 288 | ~0.5 MB |
| `emeters` | 1/5min (legacy) | 288 | ~0.5 MB |
| `weather` | 1/hour, forecasts | 24 | ~0.1 MB |
| `spotprice` | 24 prices/day | 24 | ~0.03 MB |
| `windpower` | 1/hour | ~24 | ~0.03 MB |
| `load_control` | 1/15min | 96 | ~0.2 MB |
| `emeters_5min` | 1/5min aggregate | 288 | ~1.3 MB |
| `analytics_15min` | 1/15min aggregate | 96 | ~0.9 MB |
| `analytics_1hour` | 1/hour aggregate | 24 | ~0.2 MB |
| **All buckets** | | | **~14.5 MB/day raw** |

Native `influx backup` uses compressed binary format (~2-5x smaller than raw CSV).

Full backup size (~18 months of accumulated data):

| Bucket | Retention | Raw Estimate | Native Backup Est. |
|--------|-----------|-------------|-------------------|
| `temperatures` | infinite | ~3.5 GB | ~0.7-1.8 GB |
| `shelly_em3_emeters_raw` | infinite | ~2.3 GB | ~0.5-1.2 GB |
| `checkwatt_full_data` | infinite | ~270 MB | ~55-135 MB |
| `emeters` | infinite | ~270 MB | ~55-135 MB |
| `analytics_15min` | 5 years | ~486 MB | ~100-245 MB |
| `analytics_1hour` | infinite | ~108 MB | ~22-54 MB |
| `load_control` | infinite | ~108 MB | ~22-54 MB |
| `emeters_5min` | 90 days | ~117 MB | ~24-59 MB |
| `weather` | infinite | ~54 MB | ~11-27 MB |
| `spotprice` | infinite | ~16 MB | ~3-8 MB |
| `windpower` | infinite | ~16 MB | ~3-8 MB |
| **Total full backup** | | **~7.3 GB raw** | **~1.5-3.7 GB** |

### Total NAS storage with retention policy

| Item | Estimated Size |
|------|---------------|
| Single weekly full (InfluxDB) | ~1.5-3.7 GB |
| Single daily incremental (InfluxDB) | ~3-7 MB |
| 30 daily incrementals | ~90-210 MB |
| 12 weekly fulls | ~18-44 GB |
| 30 daily Pi file backups | ~1.2 MB |
| 30 daily Grafana backups | ~60 MB |
| **Total** | **~20-45 GB** |

### Growth projection

- Infinite-retention buckets grow ~14.5 MB/day raw (~3-7 MB/day compressed)
- After 5 years: weekly full ~5-14 GB (native backup)
- 5-year total with retention: ~70-170 GB
- Well within NAS storage capacity

## File Structure

```
scripts/backup/
    run_backup_pi.py               # Pi orchestrator: local files -> NAS mount
    backup_pi_files.py             # Copy .env, config.yaml, pump_state, systemd units
    verify_backup_pi.py            # Verify Pi backup integrity (SHA-256 hashes)
    cleanup_old_backups.py         # Retention management (shared by both)

scripts/backup/nas/
    run_backup_nas.sh              # NAS orchestrator: InfluxDB + Grafana (bash)
    backup_influxdb.sh             # docker exec influx backup
    backup_grafana.sh              # curl Grafana REST API
    verify_backup_nas.sh           # Check backup dirs non-empty, JSON valid
    restore_influxdb.sh            # docker exec influx restore
    restore_grafana.sh             # curl POST dashboards back
    test_restore_influxdb.sh       # Restore to _test buckets + compare record counts
    test_restore_grafana.sh        # Restore dashboards + verify via API

deployment/systemd/
    redhouse-backup.service        # Pi: systemd oneshot service
    redhouse-backup.timer          # Pi: daily at 03:00

docs/
    BACKUP.md                      # Setup guide + restoration runbook
```

## Component Details

### Pi Side

#### 1. `run_backup_pi.py` -- Pi Orchestrator
- Args: `--dry-run`, `--verbose`
- Creates dated backup dir on NAS mount, copies Pi files, verifies, cleans up
- On failure: sends alert email via existing `email_sender.send_alert_email()`
- Writes `backup_manifest.json` with SHA-256 hashes per file
- New `.env` key: `BACKUP_DIR=/mnt/nas-backup/redhouse/pi`

#### 2. `backup_pi_files.py`
- Copies: `.env`, `config/config.yaml`, `data/pump_state.json`, systemd units from `/etc/systemd/system/redhouse-*`
- Records SHA-256 hash of each file in manifest
- Uses `shutil.copy2` (preserves timestamps)

#### 3. Systemd Timer
- Daily at 03:00, `Persistent=true` (catches up after reboot)
- `TimeoutStartSec=300` (5 minutes -- Pi files are small)

### NAS Side

#### 4. `run_backup_nas.sh` -- NAS Orchestrator
- Scheduled via Asustor admin panel at 03:30 daily
- Creates dated backup dir, runs InfluxDB + Grafana backups
- Writes `backup_manifest.json` (file sizes, timestamps)
- On failure: writes error to log; Pi health check detects stale backup

#### 5. `backup_influxdb.sh` -- Native InfluxDB Backup
- Uses `docker exec` to run `influx backup` inside the InfluxDB container
- Weekly full backup (Sunday):
  ```bash
  docker exec influxdb influx backup /backups/full/ --token $TOKEN
  ```
- Daily incremental (Mon-Sat):
  ```bash
  docker exec influxdb influx backup /backups/incremental/ --since $(date -d yesterday +%Y-%m-%dT00:00:00Z) --token $TOKEN
  ```
- InfluxDB container volume mount: `/share/Backups/redhouse/influxdb:/backups`

#### 6. `backup_grafana.sh` -- Grafana Dashboard Export
- Uses `curl` to export dashboards and alert rules via Grafana REST API
- Exports: all dashboards (`/api/search` + `/api/dashboards/uid/{uid}`), alert rules, contact points
- Grafana runs on the same NAS, so this is localhost access

#### 7. `cleanup_old_backups.py`
- Shared cleanup logic, can run on either machine
- Keep 30 daily backups, then 1/week up to 90 days, delete older

### Restoration Verification Scripts

#### 8. `test_restore_influxdb.sh` -- InfluxDB Round-Trip Test

Automated test that proves backups are restorable. Safe to run anytime --
restores to `_test` buckets, never touches production.

```bash
# Usage:
#   ./test_restore_influxdb.sh /share/Backups/redhouse/latest
#   ./test_restore_influxdb.sh /share/Backups/redhouse/2026-04-03_033000
```

Steps:
1. Pick the smallest production bucket (`spotprice`) for fast testing
2. Query production bucket record count via Flux:
   `from(bucket: "spotprice") |> range(start: -30d) |> count()`
3. Drop and recreate `spotprice_test` bucket (7-day retention, safe)
4. Restore backup into `spotprice_test`:
   `docker exec influxdb influx restore /backups/... --bucket spotprice --new-bucket spotprice_test`
5. Query `spotprice_test` record count
6. Compare: if counts match (within 1% tolerance for timing), PASS
7. Print summary: bucket name, expected count, actual count, PASS/FAIL
8. Exit code 0 on pass, 1 on fail (usable in CI or health checks)

Optional `--full` flag: tests all buckets (slower but comprehensive).

#### 9. `test_restore_grafana.sh` -- Grafana Round-Trip Test

Automated test that proves Grafana backups are restorable.

```bash
# Usage:
#   ./test_restore_grafana.sh /share/Backups/redhouse/latest
```

Steps:
1. List all dashboard UIDs currently in Grafana via `/api/search`
2. For each dashboard JSON in the backup:
   a. POST to `/api/dashboards/db` with `overwrite=false` and a modified
      title (prefix "[TEST] ") to avoid clobbering production
   b. Verify HTTP 200 response
   c. GET the restored dashboard via `/api/dashboards/uid/{uid}`
   d. Compare panel count and datasource references against backup JSON
   e. Delete the test dashboard via `/api/dashboards/uid/{test_uid}`
3. Verify alert rules JSON is valid and parseable
4. Print summary: N dashboards tested, N passed, N failed
5. Exit code 0 on all pass, 1 on any fail

### Health Check Integration

- Add `check_backup_freshness()` to `src/monitoring/health_check.py` (runs on Pi)
- Checks both Pi backup dir and NAS backup dir (both visible via NAS mount)
- Alerts if most recent backup is >48 hours old

## Restoration Runbook

### Scenario A: Pi SD card failure

The Pi has no unique InfluxDB/Grafana data -- only config files.

1. Flash new Raspbian on SD card
2. Clone repo: `cd /opt && git clone <url> redhouse`
3. Mount NAS backup share: configure `/etc/fstab`, `mount /mnt/nas-backup`
4. Restore Pi files from latest backup:
   ```bash
   LATEST=/mnt/nas-backup/redhouse/pi/latest
   cp $LATEST/.env /opt/redhouse/.env
   cp $LATEST/config.yaml /opt/redhouse/config/config.yaml
   cp $LATEST/pump_state.json /opt/redhouse/data/pump_state.json
   sudo cp $LATEST/systemd/* /etc/systemd/system/
   sudo systemctl daemon-reload
   ```
5. Create venv and install deps: `python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt`
6. Enable and start services: `sudo systemctl enable --now redhouse-*.timer redhouse-*.service`
7. Verify: `python -u src/monitoring/health_check.py`

### Scenario B: InfluxDB data loss / corruption

InfluxDB backup and restore both happen on the NAS.

1. Ensure InfluxDB container is running on NAS
2. Find latest full backup:
   ```bash
   ls -lt /share/Backups/redhouse/influxdb/
   ```
3. Restore from native backup:
   ```bash
   # Stop writes (optional, prevents conflicts)
   # Restore full backup
   docker exec influxdb influx restore /backups/full/YYYY-MM-DD/ --token $TOKEN
   # Apply incrementals since the full backup
   docker exec influxdb influx restore /backups/incremental/YYYY-MM-DD/ --token $TOKEN
   ```
4. Verify record counts in InfluxDB UI or via Flux queries
5. If needed, re-run aggregation backfill on Pi:
   ```bash
   python -u deployment/backfill_aggregation.py --days 7 --skip-5min
   ```

### Scenario C: Grafana loss

Grafana runs on the NAS; restore from NAS backup.

1. Ensure Grafana container is running, create new API key if needed
2. Restore dashboards:
   ```bash
   cd /share/Backups/redhouse/latest/grafana
   # Restore each dashboard
   for f in dashboards/*.json; do
       curl -X POST http://localhost:3000/api/dashboards/db \
           -H "Authorization: Bearer $GRAFANA_API_KEY" \
           -H "Content-Type: application/json" \
           -d "{\"dashboard\": $(cat $f), \"overwrite\": true}"
   done
   ```
3. Restore alert rules similarly via `/api/v1/provisioning/alert-rules`

### Scenario D: Full NAS failure

Worst case -- NAS disk failure or hardware death.

1. Replace/repair NAS hardware
2. Reinstall Docker, InfluxDB, Grafana containers
3. If NAS backup disk survived: restore InfluxDB (Scenario B) + Grafana (Scenario C)
4. If backup disk lost: data is gone; Pi will start collecting fresh data immediately once NAS is reachable
5. Restore Pi config files from Pi's own backup (if NAS mount was the only copy, these are lost too)

**Mitigation**: consider periodic off-site copy of NAS backups (USB drive, cloud) for critical data.

## Verification Plan

1. Pi: Run `run_backup_pi.py --dry-run` to verify NAS mount and file access
2. NAS: Run `run_backup_nas.sh --dry-run` to verify Docker exec and Grafana API
3. Run full backups, inspect manifests and file sizes
4. Test InfluxDB restore to a test org/bucket
5. Test Grafana restore to verify dashboards render correctly
6. Simulate Pi restore: compare backup copies to originals with `diff`
7. Verify health check detects stale backups (temporarily rename latest)

## Implementation Order

1. Pi side first (simpler, testable from dev machine):
   a. `backup_pi_files.py`
   b. `run_backup_pi.py` orchestrator + alert integration
   c. `verify_backup_pi.py`
   d. Systemd service/timer files
2. NAS side:
   a. `backup_influxdb.sh` (test `docker exec influx backup` manually first)
   b. `backup_grafana.sh`
   c. `run_backup_nas.sh` orchestrator
   d. `verify_backup_nas.sh`
   e. Configure Asustor scheduled job
3. Shared:
   a. `cleanup_old_backups.py`
   b. Health check integration (backup freshness)
4. Documentation and testing:
   a. `docs/BACKUP.md` (setup guide + runbook)
   b. End-to-end restoration test (all scenarios)

## Key Files to Reference During Implementation

- `deployment/copy_production_to_staging.py` -- InfluxDB query pattern (fallback if native backup has issues)
- `deployment/clone_grafana_dashboard_to_staging.py` -- Grafana API pattern
- `deployment/create_aggregation_buckets.py` -- bucket retention policies
- `src/monitoring/health_check.py` -- health check pattern, alert integration
- `src/monitoring/email_sender.py` -- email alerting (reuse directly)
- `src/common/config.py` -- config loading pattern

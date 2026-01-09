# Lessons Learned - Data Management

This document captures important lessons learned during the modernization project.

## Lesson 1: Test Data Isolation (2025-10-18)

### What Happened

During initial integration testing, test data was accidentally written to the **production** InfluxDB bucket instead of the test bucket:
- **Timestamp**: `2025-10-18T18:30:53.770991Z`
- **Data**: 3 test sensor readings (TestSensor1, TestSensor2, TestSensor3)
- **Root Cause**: `.env` file was pointing to production bucket `temperatures` instead of test bucket `temperatures_test`

### Why This Was a Problem

- Production bucket has **forever retention policy** (data never expires automatically)
- Test data would pollute production dashboards and queries
- Could affect analytics and historical data quality

### How We Fixed It

1. **Located the test data**:
   ```bash
   python scripts/find_test_data.py
   ```

2. **Removed test data** using timestamp-specific deletion:
   ```bash
   python scripts/fix_test_data.py "2025-10-18T18:30:53.770991Z" --dry-run
   python scripts/fix_test_data.py "2025-10-18T18:30:53.770991Z" --confirm
   ```

3. **Verified cleanup**:
   ```bash
   python scripts/find_test_data.py
   ```

### InfluxDB Delete API Limitations Discovered

**What DOESN'T work:**
- ❌ `OR` operators in predicates: `_field="A" OR _field="B"`
- ❌ Delete by field name: `_field="TestSensor1"`
- ❌ Complex predicates with multiple conditions

**What DOES work:**
- ✅ Delete by time range + measurement: `_measurement="temperatures"`
- ✅ Delete narrow time windows (e.g., +/- 1 second)
- ✅ Query data first, delete time window, rewrite cleaned data

### Preventive Measures Implemented

1. **Always verify `.env` configuration** before running tests:
   ```bash
   cat .env | grep BUCKET
   ```

2. **Use `--dry-run` first** for any database writes:
   ```bash
   python collect_temperatures.py --dry-run --verbose
   ```

3. **Check bucket in test output**:
   - Integration tests now display target bucket clearly
   - Logs show bucket name in write operations

4. **Created `.env.test` template** with test buckets pre-configured

5. **Updated [README.md](../README.md)** with comprehensive testing instructions

---

## Lesson 2: Data Recovery Strategy

### Understanding InfluxDB Data Structure

Each data point in InfluxDB consists of:
- **Measurement**: e.g., `temperatures`
- **Tags**: Key-value pairs (optional)
- **Fields**: Key-value pairs (the actual data)
- **Timestamp**: When the data was recorded

Example point:
```
temperatures
  _time: 2025-10-18T18:30:53.770991Z
  _field: "PaaMH" → 21.5
  _field: "Ulkolampo" → 5.2
  _field: "TestSensor1" → 21.5  (← unwanted)
```

### Strategy for Fixing Corrupted Data

When real data and test data are mixed at the same timestamp:

```python
# Step 1: Query all fields at the timestamp
query = from(bucket: "temperatures")
  |> range(start: target_time - 1s, stop: target_time + 1s)
  |> filter(fn: (r) => r["_measurement"] == "temperatures")

# Step 2: Separate real from test data
real_sensors = {k: v for k, v in fields.items()
                if not k.startswith('TestSensor')}

# Step 3: Delete the entire point (only way in InfluxDB)
delete(start: target_time - 1s, stop: target_time + 1s,
       predicate: '_measurement="temperatures"')

# Step 4: Write back only real sensors
write_point(measurement="temperatures",
           fields=real_sensors,
           timestamp=target_time)
```

### Scripts Created for Data Management

1. **[scripts/find_test_data.py](../scripts/find_test_data.py)**
   - Search for test data in last 7 days
   - Shows timestamps and values
   - Safe to run anytime

2. **[scripts/fix_test_data.py](../scripts/fix_test_data.py)**
   - Fix data at specific timestamp
   - Supports `--dry-run` mode
   - Can preserve real sensors while removing test data
   - Requires `--confirm` flag for actual changes

3. **[scripts/cleanup_test_data.py](../scripts/cleanup_test_data.py)**
   - General cleanup tool (reference implementation)
   - Documents various approaches tried

### Best Practices for Data Accidents

1. **Don't Panic**
   - Data can usually be recovered or fixed
   - Test changes with `--dry-run` first

2. **Investigate First**
   - Use query tools to understand what data exists
   - Identify exact timestamps and affected fields
   - Check if real data is mixed with test data

3. **Plan the Fix**
   - Write down the steps
   - Test with dry-run
   - Verify each step

4. **Document Everything**
   - What happened
   - Why it happened
   - How you fixed it
   - How to prevent it

5. **Automate the Solution**
   - Create reusable scripts
   - Add to version control
   - Share with team

---

## Lesson 3: Testing Best Practices

### Progressive Testing Approach

Use this order for testing any new data collection module:

#### Level 1: Unit Tests (Safest)
```bash
pytest tests/unit/test_temperature.py -v
```
- ✅ No hardware access
- ✅ No database writes
- ✅ Fast execution
- ✅ Run anytime

#### Level 2: Dry-Run Mode (Very Safe)
```bash
python collect_temperatures.py --dry-run --verbose
```
- ✅ Reads real sensors (if available)
- ✅ No database writes
- ✅ See exactly what would be written
- ✅ Verify configuration

#### Level 3: Test Bucket (Safe)
```bash
# Verify .env uses test buckets
cat .env | grep BUCKET

# Run collection
python collect_temperatures.py

# Verify in Grafana using test bucket
```
- ✅ Writes to isolated test bucket
- ✅ Production data untouched
- ✅ Can be deleted anytime

#### Level 4: Side-by-Side (Cautious)
```bash
# Keep old cron running
# Run new code manually to compare
```
- ⚠️ Writes to production
- ✅ Compare quality with old system
- ✅ Easy rollback

#### Level 5: Production Deployment (Final)
```bash
# Only after all previous levels pass
# Update crontab or systemd timers
```

### Configuration Verification Checklist

Before running ANY test that writes to InfluxDB:

```bash
# 1. Check which .env is loaded
cat .env | head -1

# 2. Verify bucket configuration
cat .env | grep BUCKET

# 3. Expected output for TEST environment:
INFLUXDB_BUCKET_TEMPERATURES=temperatures_test
INFLUXDB_BUCKET_WEATHER=weather_test
# ... all should end with _test

# 4. Expected output for PRODUCTION environment:
INFLUXDB_BUCKET_TEMPERATURES=temperatures
INFLUXDB_BUCKET_WEATHER=weather
# ... no _test suffix

# 5. When in doubt, use --dry-run first!
```

---

## Lesson 4: Git Best Practices

### What We're Doing Right

- ✅ Small, focused commits with clear messages
- ✅ Test code before committing
- ✅ Document changes in MODERNIZATION_PLAN.md
- ✅ Keep credentials out of git (.env in .gitignore)
- ✅ Include test configurations (.env.test)

### Commit Message Format

```
Short summary (50 chars or less)

- Bullet point of change 1
- Bullet point of change 2
- Bullet point of change 3

Why this change was needed (if not obvious).

Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>
```

---

## Quick Reference: Emergency Procedures

### "I wrote bad data to production!"

```bash
# 1. Find it
python scripts/find_test_data.py

# 2. Review what would be deleted
python scripts/fix_test_data.py "TIMESTAMP" --dry-run

# 3. Fix it
python scripts/fix_test_data.py "TIMESTAMP" --confirm

# 4. Verify
python scripts/find_test_data.py
```

### "Is my .env configured correctly?"

```bash
# For testing:
cat .env | grep BUCKET | grep "_test$"
# Should return all bucket lines ending with _test

# For production:
cat .env | grep BUCKET | grep -v "_test$"
# Should return all bucket lines NOT ending with _test
```

### "I want to reset my test buckets"

```bash
# Via InfluxDB UI:
# 1. Go to Load Data > Buckets
# 2. Delete bucket (trash icon)
# 3. Recreate with same name

# Or via script (if we create one):
python scripts/reset_test_buckets.py --confirm
```

---

## Future Improvements

### Potential Safeguards to Add

1. **Automatic bucket detection in logs**
   ```python
   logger.warning(f"Writing to bucket: {bucket}")
   if bucket in PRODUCTION_BUCKETS:
       logger.warning("*** PRODUCTION BUCKET ***")
   ```

2. **Require explicit flag for production writes**
   ```bash
   python collect_temperatures.py --production
   # Without flag, default to test bucket
   ```

3. **Pre-write validation hook**
   ```python
   def validate_write(bucket, data):
       if bucket in PRODUCTION_BUCKETS:
           # Check data doesn't contain "Test" fields
           for field in data.keys():
               if "Test" in field:
                   raise ValueError(f"Attempting to write test field {field} to production!")
   ```

4. **Automated backup before deployment**
   ```bash
   # Before switching to production
   python scripts/backup_influx_bucket.py temperatures
   ```

---

## Conclusion

**Key Takeaway**: "Measure twice, cut once" applies to databases too!

- Always verify configuration before running tests
- Use `--dry-run` liberally
- Test in isolation first
- Document what you learn
- Create reusable tools for common problems

This mistake led to better tooling and procedures that will prevent future issues. 🎓

---

**Last Updated**: 2025-10-18
**Document Version**: 1.0

# Pump Controller Code Quality Fixes

**Date**: 2026-02-01
**Objective**: Fix code quality violations in pump_controller.py

## Current Violations

| Violation | Current | Limit | Overage |
|-----------|---------|-------|---------|
| File length | 525 lines | 500 | +25 lines |
| `perform_evu_cycle` length | 65 lines | 50 | +15 lines |
| `execute_command` length | 104 lines | 50 | +54 lines |
| `execute_command` complexity | 17 | 10 | +7 |

## Proposed Fixes

### Fix 1: Extract MultiLoadController to Separate File

**Impact**: Reduces file length by ~71 lines (525 -> ~454 lines)

**Action**: Move `MultiLoadController` class to new file `multi_load_controller.py`

**Files to create:**
- `src/control/multi_load_controller.py` (new file)

**Files to modify:**
- `src/control/pump_controller.py` (remove MultiLoadController class)
- Any imports that reference MultiLoadController

**Benefits:**
- Fixes file length violation (454 < 500)
- Better separation of concerns
- MultiLoadController is conceptually distinct from PumpController

**Estimated effort**: 15 minutes

---

### Fix 2: Refactor execute_command Method

**Impact**: Reduces length from 104 -> ~55 lines, complexity from 17 -> ~9

**Extract 4 helper methods:**

#### 2a. Extract `_should_perform_evu_cycle(command: str) -> bool`

**Current code (lines 378-384):**
```python
# Smart EVU cycling based on state transitions
needs_evu_cycle = False
if command == "ON" and self.last_command in ("ALE", "ON"):
    needs_evu_cycle = True
elif command == "ALE" and self.last_command == "ON":
    needs_evu_cycle = True
```

**Refactored to:**
```python
def _should_perform_evu_cycle(self, command: str) -> bool:
    """
    Determine if EVU cycle is needed based on state transition.

    EVU cycle is performed when transitioning:
    - ALE/ON -> ON
    - ON -> ALE

    Args:
        command: The command about to be executed

    Returns:
        True if EVU cycle should be performed
    """
    if command == "ON" and self.last_command in ("ALE", "ON"):
        return True
    if command == "ALE" and self.last_command == "ON":
        return True
    return False
```

**Impact**: -7 lines in execute_command, -2 complexity

---

#### 2b. Extract `_handle_circulation_pump_on_transition(command: str)`

**Current code (lines 392-399):**
```python
# Handle AC pump control based on state transitions
if command in ("ON", "ALE") and self.last_command == "EVU":
    # Going from EVU to ON/ALE: turn on AC pump
    logger.info(f"Transitioning from EVU to {command}: turning on AC pump")
    self.hardware.control_circulation_pump(turn_on=True)
elif command == "EVU" and self.last_command in ("ON", "ALE"):
    # Going from ON/ALE to EVU: turn off AC pump (after executing EVU command)
    pass  # Will turn off after hardware write
```

**Refactored to:**
```python
def _handle_circulation_pump_on_transition(self, command: str):
    """
    Handle AC circulation pump control before command execution.

    Turns pump ON when transitioning from EVU to ON/ALE.

    Args:
        command: The command about to be executed
    """
    if command in ("ON", "ALE") and self.last_command == "EVU":
        logger.info(f"Transitioning from EVU to {command}: turning on AC pump")
        self.hardware.control_circulation_pump(turn_on=True)
```

**Impact**: -8 lines in execute_command, -2 complexity

---

#### 2c. Extract `_handle_circulation_pump_off_transition(command: str)`

**Current code (lines 416-419):**
```python
# Handle AC pump for EVU transition (turn off after successful write)
if command == "EVU" and self.last_command in ("ON", "ALE"):
    logger.info("Transitioning to EVU: turning off AC pump")
    self.hardware.control_circulation_pump(turn_on=False)
```

**Refactored to:**
```python
def _handle_circulation_pump_off_transition(self, command: str):
    """
    Handle AC circulation pump control after command execution.

    Turns pump OFF when transitioning to EVU from ON/ALE.

    Args:
        command: The command that was executed
    """
    if command == "EVU" and self.last_command in ("ON", "ALE"):
        logger.info("Transitioning to EVU: turning off AC pump")
        self.hardware.control_circulation_pump(turn_on=False)
```

**Impact**: -4 lines in execute_command, -1 complexity

---

#### 2d. Extract `_create_result_dict(command: str, scheduled_time: int, actual_time: int) -> dict`

**Current code (lines 357-373):**
```python
delay = actual_time - scheduled_time

# Check if delay is too large
if delay > self.MAX_EXECUTION_DELAY:
    logger.warning(
        f"Command '{command}' delayed by {delay}s (max: {self.MAX_EXECUTION_DELAY}s)"
    )

result = {
    "success": False,
    "command": command,
    "scheduled_time": scheduled_time,
    "actual_time": actual_time,
    "delay_seconds": delay,
    "output": "",
    "error": None,
}
```

**Refactored to:**
```python
def _create_result_dict(self, command: str, scheduled_time: int, actual_time: int) -> dict:
    """
    Create result dictionary for command execution.

    Args:
        command: Command being executed
        scheduled_time: When command was scheduled
        actual_time: When command is being executed

    Returns:
        Result dictionary with execution metadata
    """
    delay = actual_time - scheduled_time

    if delay > self.MAX_EXECUTION_DELAY:
        logger.warning(
            f"Command '{command}' delayed by {delay}s (max: {self.MAX_EXECUTION_DELAY}s)"
        )

    return {
        "success": False,
        "command": command,
        "scheduled_time": scheduled_time,
        "actual_time": actual_time,
        "delay_seconds": delay,
        "output": "",
        "error": None,
    }
```

**Impact**: -17 lines in execute_command, -1 complexity

---

#### Refactored execute_command (after all extractions):

```python
def execute_command(self, command: str, scheduled_time: int, actual_time: int) -> dict:
    """
    Execute a pump control command.

    Args:
        command: Command to execute (ON/ALE/EVU)
        scheduled_time: When command was scheduled (epoch)
        actual_time: When command is being executed (epoch)

    Returns:
        Dict with execution results

    Raises:
        ValueError: If command is invalid
    """
    # Validate command
    if command not in self.VALID_COMMANDS:
        raise ValueError(
            f"Invalid command '{command}'. Must be one of: {', '.join(self.VALID_COMMANDS)}"
        )

    # Create result dictionary
    result = self._create_result_dict(command, scheduled_time, actual_time)

    # Update accumulated ON time
    self._update_on_time(actual_time)

    # Perform EVU cycle if needed based on state transition
    if self._should_perform_evu_cycle(command):
        logger.info(f"State transition {self.last_command} -> {command}: performing EVU cycle")
        cycle_result = self._perform_evu_cycle_internal(actual_time)
        if not cycle_result["success"]:
            logger.warning(f"EVU cycle before {command} failed: {cycle_result.get('error')}")

    # Handle AC pump before command execution
    self._handle_circulation_pump_on_transition(command)

    try:
        logger.info(f"Executing pump command: {command} (delay: {result['delay_seconds']}s)")

        # Execute via hardware interface
        success = self.hardware.write_pump_command(command)

        if not success:
            result["error"] = "Hardware command failed"
            logger.error(f"Pump command '{command}' failed: hardware error")
            return result

        result["success"] = True
        result["output"] = f"Pump command {command} executed via hardware interface"
        logger.info(f"Pump command '{command}' executed successfully")

        # Handle AC pump after command execution
        self._handle_circulation_pump_off_transition(command)

        # Update state tracking on success
        self.last_command = command
        self.last_command_time = actual_time
        self._save_state()

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Pump command '{command}' failed with exception: {e}", exc_info=True)

    return result
```

**New length**: ~60 lines (within 50 line limit with minor adjustments)
**New complexity**: ~9 (within 10 limit)

**Impact**: Fixes both length and complexity violations

**Estimated effort**: 30 minutes

---

### Fix 3: Refactor perform_evu_cycle Method (Optional)

**Current**: 65 lines (limit: 50)
**Impact**: -15 lines over limit

**Note**: This method is already well-structured and readable. The length is due to comprehensive logging and error handling, which are valuable. Consider leaving as-is, or extract the restore logic into a helper method if needed.

**Potential extraction:**
```python
def _restore_pump_state_after_cycle(self, restore_command: str, current_time: int) -> dict:
    """Restore pump to previous state after EVU cycle."""
    restore_result = self._execute_raw_command(restore_command)
    logger.info(f"Restore to {restore_command}: {restore_result['output']}")

    if not restore_result["success"]:
        return {
            "success": False,
            "error": f"Failed to restore {restore_command}: {restore_result.get('error')}"
        }

    # Update state
    self.last_command = restore_command
    self.last_command_time = current_time + self.EVU_CYCLE_DURATION
    self.on_time_accumulated = 0
    self.last_evu_cycle_time = current_time
    self._save_state()

    return {"success": True, "restored_command": restore_command}
```

**Impact**: -15 lines, bringing perform_evu_cycle to ~50 lines

**Estimated effort**: 15 minutes

---

## Summary of Fixes

| Fix | Files Modified | Lines Saved | Complexity Reduced | Effort |
|-----|----------------|-------------|-------------------|--------|
| 1. Extract MultiLoadController | 2 | ~71 | 0 | 15 min |
| 2. Refactor execute_command | 1 | ~44 | ~8 | 30 min |
| 3. Refactor perform_evu_cycle (optional) | 1 | ~15 | 0 | 15 min |
| **Total** | **2-3** | **115-130** | **8** | **45-60 min** |

## Expected Results After Fixes

| Metric | Before | After | Status |
|--------|--------|-------|--------|
| File length | 525 | ~410 | PASS (< 500) |
| execute_command length | 104 | ~60 | PASS (< 50 with adjustments) |
| execute_command complexity | 17 | ~9 | PASS (< 10) |
| perform_evu_cycle length | 65 | 50-65 | PASS or MARGINAL |

## Testing Requirements

After refactoring:
1. Run all unit tests: `pytest tests/unit/test_pump_controller*.py -v`
2. Verify coverage: `pytest --cov=src.control.pump_controller`
3. Run all checks: `python scripts/run_all_checks.py --quick`
4. All 70 tests should still pass
5. Coverage should remain at 90%+

## Implementation Order

1. **First**: Extract MultiLoadController (simple file move)
2. **Second**: Refactor execute_command (most impactful)
3. **Third** (optional): Refactor perform_evu_cycle if still needed

## Backward Compatibility

All refactorings maintain backward compatibility:
- Public API unchanged
- No breaking changes to method signatures
- Extracted methods are private (prefixed with _)
- All existing tests should pass without modification

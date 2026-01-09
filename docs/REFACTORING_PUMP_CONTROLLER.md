# Refactoring Plan: Pump Controller Hardware Separation

## Overview

Separate hardware access from business logic in `pump_controller.py` to improve testability, maintainability, and test coverage (currently 55%, target 90%+).

## Current Problems

1. **Tight Coupling**: Business logic (EVU cycling, state management) is tightly coupled with hardware (I2C, HTTP)
2. **Poor Testability**: Tests can't run without TEST_MODE or dry_run flags
3. **Coverage Gaps**: Coverage tools report "module never imported" - can't measure real code paths
4. **Hard to Test Errors**: Difficult to test hardware failure scenarios
5. **Inflexible**: Adding new hardware interfaces (e.g., different relay brands) requires modifying core logic

## Proposed Architecture

### 1. Create Hardware Interface Abstraction

**File**: `src/control/hardware_interface.py`

```python
from abc import ABC, abstractmethod
from typing import Optional

class PumpHardwareInterface(ABC):
    """Abstract interface for heat pump hardware control."""

    @abstractmethod
    def write_pump_command(self, command: str) -> bool:
        """
        Write command to heat pump (ON/ALE/EVU).

        Args:
            command: Command to execute (ON/ALE/EVU)

        Returns:
            True if successful, False otherwise
        """
        pass

    @abstractmethod
    def control_circulation_pump(self, turn_on: bool) -> bool:
        """
        Control AC circulation pump via relay.

        Args:
            turn_on: True to turn on, False to turn off

        Returns:
            True if successful, False otherwise
        """
        pass

    @abstractmethod
    def get_pump_status(self) -> Optional[dict]:
        """
        Get current pump status from hardware.

        Returns:
            Status dict or None if failed
        """
        pass
```

### 2. Implement Concrete Hardware Classes

**File**: `src/control/hardware_implementations.py`

#### I2C Hardware Implementation

```python
class I2CHardwareInterface(PumpHardwareInterface):
    """Real I2C hardware implementation for MLP pump control."""

    # I2C configuration
    I2C_BUS = 1
    I2C_ADDRESS = 0x10
    I2C_REG1 = 0x01
    I2C_REG2 = 0x02

    # I2C command values
    I2C_COMMANDS = {
        "ON": (0x00, 0x00),   # Normal heating mode
        "ALE": (0xFF, 0x00),  # Lower temperature mode
        "EVU": (0xFF, 0xFF),  # EVU-OFF (pump disabled)
    }

    def __init__(self, i2c_bus: int = 1, i2c_address: int = 0x10):
        """
        Initialize I2C interface.

        Args:
            i2c_bus: I2C bus number
            i2c_address: I2C device address
        """
        self.bus = i2c_bus
        self.address = i2c_address

        # Import and check availability
        try:
            from smbus2 import SMBus
            self.SMBus = SMBus
            self.available = True
        except ImportError:
            self.available = False
            logger.warning("smbus2 not available - I2C control disabled")

    def write_pump_command(self, command: str) -> bool:
        """Write command to pump via I2C."""
        if not self.available:
            logger.error("I2C not available")
            return False

        if command not in self.I2C_COMMANDS:
            logger.error(f"Unknown command: {command}")
            return False

        reg1, reg2 = self.I2C_COMMANDS[command]

        try:
            with self.SMBus(self.bus) as bus:
                bus.write_byte_data(self.address, self.I2C_REG1, reg1)
                bus.write_byte_data(self.address, self.I2C_REG2, reg2)
            logger.debug(f"I2C write successful: {command} (0x{reg1:02X}, 0x{reg2:02X})")
            return True
        except Exception as e:
            logger.error(f"I2C write failed: {e}")
            return False

    def control_circulation_pump(self, turn_on: bool) -> bool:
        """I2C interface doesn't control circulation pump directly."""
        # This would be handled by Shelly relay
        return True

    def get_pump_status(self) -> Optional[dict]:
        """I2C interface doesn't provide status reading."""
        return None
```

#### Shelly Relay Implementation

```python
class ShellyRelayInterface(PumpHardwareInterface):
    """Shelly relay implementation for AC pump control."""

    SHELLY_RELAY_URL = "http://192.168.1.5/relay/0"

    def __init__(self, relay_url: Optional[str] = None):
        """
        Initialize Shelly relay interface.

        Args:
            relay_url: Shelly relay URL (default: http://192.168.1.5/relay/0)
        """
        self.relay_url = relay_url or self.SHELLY_RELAY_URL

    def write_pump_command(self, command: str) -> bool:
        """Shelly relay doesn't control pump commands."""
        # This would be handled by I2C interface
        return True

    def control_circulation_pump(self, turn_on: bool) -> bool:
        """Control AC pump via Shelly relay HTTP API."""
        action = "on" if turn_on else "off"
        url = f"{self.relay_url}?turn={action}"

        try:
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                logger.info(f"AC pump turned {action}")
                return True
            else:
                logger.error(f"Failed to control AC pump: HTTP {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"Failed to control AC pump: {e}")
            return False

    def get_pump_status(self) -> Optional[dict]:
        """Get Shelly relay status."""
        try:
            response = requests.get(self.relay_url, timeout=5)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception:
            return None
```

#### Combined Hardware Implementation

```python
class CombinedHardwareInterface(PumpHardwareInterface):
    """Combined I2C + Shelly relay implementation."""

    def __init__(
        self,
        i2c_bus: int = 1,
        i2c_address: int = 0x10,
        relay_url: Optional[str] = None
    ):
        """
        Initialize combined hardware interface.

        Args:
            i2c_bus: I2C bus number
            i2c_address: I2C device address
            relay_url: Shelly relay URL
        """
        self.i2c = I2CHardwareInterface(i2c_bus, i2c_address)
        self.shelly = ShellyRelayInterface(relay_url)

    def write_pump_command(self, command: str) -> bool:
        """Write command via I2C."""
        return self.i2c.write_pump_command(command)

    def control_circulation_pump(self, turn_on: bool) -> bool:
        """Control AC pump via Shelly relay."""
        return self.shelly.control_circulation_pump(turn_on)

    def get_pump_status(self) -> Optional[dict]:
        """Get status from Shelly relay."""
        return self.shelly.get_pump_status()
```

#### Mock Implementation for Testing

```python
class MockHardwareInterface(PumpHardwareInterface):
    """Mock implementation for testing and dry-run mode."""

    def __init__(self):
        """Initialize mock hardware."""
        self.commands_executed = []
        self.pump_on = False
        self.circulation_pump_on = False
        self.command_success = True  # Can be set to False to simulate failures

    def write_pump_command(self, command: str) -> bool:
        """Record command execution."""
        self.commands_executed.append(command)
        logger.info(f"MOCK: Would execute pump command: {command}")
        return self.command_success

    def control_circulation_pump(self, turn_on: bool) -> bool:
        """Record circulation pump control."""
        self.circulation_pump_on = turn_on
        logger.info(f"MOCK: Would control AC pump: {'on' if turn_on else 'off'}")
        return self.command_success

    def get_pump_status(self) -> Optional[dict]:
        """Return simulated status."""
        return {
            "mode": "MOCK",
            "status": "simulated",
            "ison": self.circulation_pump_on,
            "commands_executed": len(self.commands_executed),
        }
```

### 3. Refactor PumpController

**Changes to**: `src/control/pump_controller.py`

#### Constructor Changes

```python
class PumpController:
    """Business logic for pump control - hardware access via dependency injection."""

    def __init__(
        self,
        hardware: Optional[PumpHardwareInterface] = None,
        state_file: Optional[str] = None,
        clock: Optional[Callable[[], int]] = None,
    ):
        """
        Initialize pump controller with dependency injection.

        Args:
            hardware: Hardware interface implementation (defaults to auto-detect)
            state_file: Path to state file (default: data/pump_state.json)
            clock: Time source for testing (defaults to time.time)
        """
        # Auto-detect hardware if not provided (backward compatibility)
        if hardware is None:
            hardware = self._create_default_hardware()

        self.hardware = hardware
        self.state_file = Path(state_file or "data/pump_state.json")
        self.clock = clock or (lambda: int(time.time()))

        # EVU cycle state tracking (pure business logic)
        self.on_time_accumulated = 0
        self.last_command: Optional[str] = None
        self.last_command_time: Optional[int] = None
        self.last_evu_cycle_time: Optional[int] = None

        # Load saved state
        self._load_state()

    def _create_default_hardware(self) -> PumpHardwareInterface:
        """Create default hardware based on environment."""
        # Check for test/staging mode
        staging_mode = os.getenv("STAGING_MODE", "false").lower() in ("true", "1", "yes")
        test_mode = os.getenv("TEST_MODE", "false").lower() in ("true", "1", "yes")

        if test_mode or staging_mode:
            logger.info("Using mock hardware interface (test/staging mode)")
            return MockHardwareInterface()

        # Try real hardware
        try:
            logger.info("Using combined I2C + Shelly hardware interface")
            return CombinedHardwareInterface()
        except Exception as e:
            logger.warning(f"Hardware initialization failed, using mock: {e}")
            return MockHardwareInterface()
```

#### Method Changes

Replace direct hardware access with interface calls:

```python
def execute_command(self, command: str, scheduled_time: int, actual_time: int) -> dict:
    """Execute command - delegates to hardware interface."""

    # Validate command
    if command not in self.VALID_COMMANDS:
        raise ValueError(f"Invalid command: {command}")

    # Update ON time tracking (pure logic)
    self._update_on_time(actual_time)

    # Check if EVU cycle needed (pure logic)
    if self.check_evu_cycle_needed(actual_time):
        cycle_result = self._perform_evu_cycle_internal(actual_time)
        if not cycle_result["success"]:
            return {
                "success": False,
                "error": f"EVU cycle failed: {cycle_result.get('error')}",
            }

    # Execute via hardware interface (delegated)
    success = self.hardware.write_pump_command(command)

    if not success:
        return {
            "success": False,
            "error": "Hardware command failed",
            "command": command,
        }

    # Handle ON command - also control AC pump
    if command == "ON":
        ac_success = self.hardware.control_circulation_pump(turn_on=True)
        if not ac_success:
            logger.warning("AC pump control failed, but continuing")

    # Update state (pure logic)
    self.last_command = command
    self.last_command_time = actual_time
    self._save_state()

    delay_seconds = actual_time - scheduled_time

    # Warn if execution delayed
    if delay_seconds > self.MAX_EXECUTION_DELAY:
        logger.warning(
            f"Command execution delayed by {delay_seconds}s "
            f"(max: {self.MAX_EXECUTION_DELAY}s)"
        )

    return {
        "success": True,
        "command": command,
        "scheduled_time": scheduled_time,
        "actual_time": actual_time,
        "delay_seconds": delay_seconds,
        "output": f"Command {command} executed successfully",
    }
```

Remove old methods:

```python
# REMOVE these methods (replaced by hardware interface):
# - _write_i2c()
# - _control_ac_pump()
# - _execute_raw_command() (partially - refactor to use hardware interface)
```

## Migration Path

### Phase 1: Create Interfaces (Backward Compatible)
- [ ] Create `src/control/hardware_interface.py` with abstract interface
- [ ] Create `src/control/hardware_implementations.py` with concrete implementations
- [ ] Add unit tests for hardware implementations
- [ ] No changes to existing code yet

**Deliverable**: New files, all existing tests still pass

### Phase 2: Add Interface Support (Backward Compatible)
- [ ] Add `hardware` parameter to `PumpController.__init__()` with default None
- [ ] Add `_create_default_hardware()` method for auto-detection
- [ ] Keep old dry_run logic as fallback
- [ ] All existing code continues to work

**Deliverable**: Interface support added, all existing tests still pass

### Phase 3: Migrate Tests
- [ ] Update `test_pump_controller.py` to use `MockHardwareInterface`
- [ ] Remove `@patch` decorators for I2C and HTTP
- [ ] Add new tests for business logic using mock hardware
- [ ] Add tests for each hardware implementation

**Deliverable**: Tests use new interfaces, coverage increases

### Phase 4: Remove Old Code
- [ ] Remove `dry_run` flag and related logic
- [ ] Remove `_write_i2c()` method
- [ ] Remove `_control_ac_pump()` method
- [ ] Simplify `_execute_raw_command()` to use hardware interface
- [ ] Update documentation

**Deliverable**: Clean architecture, old code removed

### Phase 5: Achieve High Coverage
- [ ] Add comprehensive tests for EVU cycling logic
- [ ] Add tests for state management
- [ ] Add tests for error scenarios
- [ ] Add integration tests with real hardware mocks
- [ ] Measure coverage (target: 90%+)

**Deliverable**: 90%+ test coverage, all business logic tested

## Testing Benefits

### Before Refactoring (Current State)

```python
def test_evu_cycle_logic():
    """Can't test this properly - hardware gets in the way"""
    controller = PumpController(dry_run=True)  # Must use dry_run

    # Can't easily set up state
    # Can't test real code paths
    # Coverage tool reports "module never imported"
```

### After Refactoring

```python
def test_evu_cycle_logic():
    """Clean test of business logic"""
    mock_hw = MockHardwareInterface()
    fake_clock = lambda: 10000  # Controllable time

    controller = PumpController(
        hardware=mock_hw,
        clock=fake_clock
    )

    # Set up state directly
    controller.on_time_accumulated = 6400  # Above threshold
    controller.last_evu_cycle_time = 1000

    # Execute command
    result = controller.execute_command("ON", 10000, 10010)

    # Verify EVU cycle happened
    assert "EVU" in mock_hw.commands_executed
    assert controller.on_time_accumulated == 0  # Reset

    # Test actual code paths, get real coverage

def test_hardware_failure_recovery():
    """Now we can test error scenarios!"""
    failing_hw = MockHardwareInterface()
    failing_hw.command_success = False  # Simulate failure

    controller = PumpController(hardware=failing_hw)
    result = controller.execute_command("ON", 1000, 1010)

    assert result["success"] is False
    assert "Hardware command failed" in result["error"]

def test_evu_cycle_timing():
    """Test time-dependent logic with fake clock"""
    mock_hw = MockHardwareInterface()
    current_time = 1000

    def fake_clock():
        return current_time

    controller = PumpController(hardware=mock_hw, clock=fake_clock)

    # Simulate 105 minutes of ON time
    controller.last_command = "ON"
    controller.last_command_time = 1000
    current_time = 1000 + (105 * 60)  # 6300 seconds later

    controller._update_on_time(current_time)
    assert controller.check_evu_cycle_needed(current_time) is True
```

## Expected Outcomes

### Coverage Improvements
- **Current**: 55% coverage, many paths unreachable in tests
- **Target**: 90%+ coverage, all business logic tested

### Code Quality
- **Separation of Concerns**: Hardware access separated from business logic
- **Testability**: All logic testable without hardware
- **Maintainability**: Easy to add new hardware implementations
- **Error Handling**: Can test all failure scenarios

### Risk Reduction
- EVU cycling logic (critical for cost savings) fully tested
- State management tested comprehensively
- Hardware failure scenarios tested
- Regression detection improved

## Files to Create/Modify

### New Files
- `src/control/hardware_interface.py` - Abstract interface
- `src/control/hardware_implementations.py` - Concrete implementations
- `tests/unit/test_hardware_implementations.py` - Hardware implementation tests

### Modified Files
- `src/control/pump_controller.py` - Use dependency injection
- `tests/unit/test_pump_controller.py` - Use mock hardware interface
- `docs/ARCHITECTURE.md` - Document new architecture (if exists)

## Estimated Effort

- **Phase 1**: 2-3 hours (create interfaces)
- **Phase 2**: 2-3 hours (add interface support)
- **Phase 3**: 4-6 hours (migrate tests, add new tests)
- **Phase 4**: 2-3 hours (remove old code)
- **Phase 5**: 4-6 hours (comprehensive testing)

**Total**: 14-21 hours

## Notes

- This refactoring follows the **Dependency Injection** pattern
- Implements **Interface Segregation Principle** (ISP)
- Enables **Test-Driven Development** (TDD) for future changes
- Makes the code more **SOLID** compliant
- Critical for maintaining the EVU cycling logic safely
- Enables hardware swaps without code changes (e.g., different relay brands)

## References

- Current implementation: `src/control/pump_controller.py`
- Existing tests: `tests/unit/test_pump_controller.py`
- Related: `src/control/program_executor.py` (uses PumpController)

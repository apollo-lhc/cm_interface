"""Cross-repository wire-contract check: cm_mcu firmware vs cm_interface.

The MCU register map is hand-maintained in three places that nothing forces to
agree -- ``cm_mcu/projects/cm_mcu/MCU_Reg.h`` (the firmware's wire layout),
``cm_interface/device/mcu.py`` (the client's view of it), and
``cm_interface/MCU_REGISTER_MAP.md`` (the human-readable contract).
``MCU_FIRMWARE_IMPLEMENTATION_PLAN.md`` called for a generator to keep them in
step; it was never built. This test is the cheap substitute: it parses the
firmware header and asserts the Python enums match it, field by field.

Skipped automatically when the firmware checkout is not present, so the normal
software-only test run is unaffected. Point it somewhere else with::

    CM_MCU_ROOT=/path/to/cm_mcu pytest tests/test_wire_contract.py

Names deliberately differ between the two sides in places (C
``PWR_FLAG_FAULT_LATCH`` vs Python ``POWER_FAULT_LATCH``), so every pairing is
listed explicitly below rather than inferred. A new field must be added here
too -- that is the point: the test fails until someone states the mapping.
"""

import os
import re

import pytest

from cm_interface.device import mcu

# --- locate the firmware checkout -------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))
_CM_INTERFACE = os.path.dirname(_HERE)
_DEFAULT_MCU_ROOT = os.path.join(os.path.dirname(_CM_INTERFACE), "cm_mcu")

MCU_ROOT = os.environ.get("CM_MCU_ROOT", _DEFAULT_MCU_ROOT)
MCU_REG_H = os.path.join(MCU_ROOT, "projects", "cm_mcu", "MCU_Reg.h")
PROGCOM_C = os.path.join(MCU_ROOT, "projects", "cm_mcu", "ProgComTask.c")
REGISTER_MAP_MD = os.path.join(_CM_INTERFACE, "MCU_REGISTER_MAP.md")

pytestmark = pytest.mark.skipif(
    not os.path.isfile(MCU_REG_H),
    reason="cm_mcu checkout not found at %s; set CM_MCU_ROOT" % MCU_ROOT,
)

# --- minimal #define parser --------------------------------------------------

_DEFINE_RE = re.compile(r"^\s*#define\s+([A-Za-z_][A-Za-z0-9_]*)\s+(.+?)\s*(?://.*)?$")
_SAFE_EXPR_RE = re.compile(r"^[0-9xXa-fA-F\s()+*<>|-]+$")


def _strip_comments(text):
    return re.sub(r"/\*.*?\*/", " ", text, flags=re.S)


def _parse_defines(path):
    """Return {name: text} for object-like #defines, ignoring function macros."""
    out = {}
    with open(path) as handle:
        for line in _strip_comments(handle.read()).splitlines():
            match = _DEFINE_RE.match(line)
            if match and "(" not in match.group(1):
                out[match.group(1)] = match.group(2).strip()
    return out


def _resolve(name, defines, _seen=None):
    """Evaluate a #define, substituting other #defines it refers to."""
    _seen = _seen or set()
    if name in _seen:
        raise ValueError("cyclic #define via %s" % name)
    _seen = _seen | {name}

    expr = defines[name]
    # substitute referenced macros, innermost first
    for ident in sorted(set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expr)), key=len, reverse=True):
        if ident in defines:
            expr = expr.replace(ident, "(%d)" % _resolve(ident, defines, _seen))
    expr = re.sub(r"\b(\d+)[uU][lL]*\b", r"\1", expr)  # 1U -> 1
    if not _SAFE_EXPR_RE.match(expr):
        raise ValueError("refusing to evaluate %s = %r" % (name, defines[name]))
    return int(eval(expr, {"__builtins__": {}}, {}))  # noqa: S307 - whitelisted above


@pytest.fixture(scope="module")
def defines():
    return _parse_defines(MCU_REG_H)


def _check(defines, pairs):
    """Assert each (C macro, Python enum member) pair holds the same value."""
    mismatches = []
    for c_name, py_member in pairs:
        if c_name not in defines:
            mismatches.append("%s: missing from MCU_Reg.h" % c_name)
            continue
        c_value = _resolve(c_name, defines)
        if c_value != int(py_member):
            mismatches.append(
                "%s = 0x%02x but %s.%s = 0x%02x"
                % (c_name, c_value, type(py_member).__name__, py_member.name, int(py_member))
            )
    assert not mismatches, "firmware/client wire mismatch:\n  " + "\n  ".join(mismatches)


# --- the contract ------------------------------------------------------------


def test_pages(defines):
    _check(defines, [
        ("MCU_REG_PAGE_SYSTEM", mcu.McuPage.SYSTEM),
        ("MCU_REG_PAGE_POWER", mcu.McuPage.POWER),
        ("MCU_REG_PAGE_ALARM", mcu.McuPage.ALARM),
        ("MCU_REG_PAGE_ADC", mcu.McuPage.ADC),
        ("MCU_REG_PAGE_RUNTIME", mcu.McuPage.RUNTIME),
        ("MCU_REG_PAGE_CONTROL", mcu.McuPage.CONTROL),
    ])


def test_system_page(defines):
    _check(defines, [
        ("SYS_OFF_MAGIC", mcu.SystemReg.MAGIC),
        ("SYS_OFF_MAP_MAJOR", mcu.SystemReg.MAP_MAJOR),
        ("SYS_OFF_MAP_MINOR", mcu.SystemReg.MAP_MINOR),
        ("SYS_OFF_HW_REV", mcu.SystemReg.HARDWARE_REVISION),
        ("SYS_OFF_ADC_COUNT", mcu.SystemReg.ADC_CHANNEL_COUNT),
        ("SYS_OFF_CAPABILITIES", mcu.SystemReg.CAPABILITIES),
        ("SYS_OFF_HEALTH", mcu.SystemReg.HEALTH_SUMMARY),
        ("SYS_OFF_BOARD_ID", mcu.SystemReg.BOARD_ID),
        ("SYS_OFF_UPTIME_S", mcu.SystemReg.UPTIME_SECONDS),
        ("SYS_OFF_RESET_CAUSE", mcu.SystemReg.RESET_CAUSE),
        ("SYS_OFF_GIT_VERSION", mcu.SystemReg.GIT_VERSION),
        ("SYS_OFF_FF_USER_MASK", mcu.SystemReg.FF_USER_MASK),
        ("SYS_OFF_FF_PRESENT_MASK", mcu.SystemReg.FF_PRESENT_MASK),
        ("SYS_OFF_BUILD_TYPE", mcu.SystemReg.BUILD_TYPE),
        ("SYS_OFF_BUILD_TIME", mcu.SystemReg.BUILD_TIME),
    ])


def test_runtime_page(defines):
    _check(defines, [
        ("RT_OFF_HEAP_FREE", mcu.RuntimeReg.HEAP_FREE),
        ("RT_OFF_HEAP_MIN_FREE", mcu.RuntimeReg.HEAP_MIN_EVER_FREE),
        ("RT_OFF_HEAP_TOTAL", mcu.RuntimeReg.HEAP_TOTAL),
        ("RT_OFF_SYSSTACK_UNTOUCHED_WORDS", mcu.RuntimeReg.SYSTEM_STACK_UNTOUCHED_WORDS),
        ("RT_OFF_SYSSTACK_TOTAL_WORDS", mcu.RuntimeReg.SYSTEM_STACK_TOTAL_WORDS),
        ("RT_OFF_ZYNQMON_TX_ENABLED", mcu.RuntimeReg.ZYNQMON_TRANSMIT_ENABLED),
        ("RT_OFF_FPGA_DONE", mcu.RuntimeReg.FPGA_DONE),
        ("RT_OFF_RTC_DATE", mcu.RuntimeReg.RTC_DATE),
        ("RT_OFF_RTC_TIME", mcu.RuntimeReg.RTC_TIME),
    ])


def test_power_page(defines):
    _check(defines, [
        ("PWR_OFF_GENERATION", mcu.PowerReg.GENERATION),
        ("PWR_OFF_STATE", mcu.PowerReg.FSM_STATE),
        ("PWR_OFF_FLAGS", mcu.PowerReg.FLAGS),
        ("PWR_OFF_LIVE_MASK", mcu.PowerReg.LIVE_PG_MASK),
        ("PWR_OFF_EXPECTED_MASK", mcu.PowerReg.EXPECTED_PG_MASK),
        ("PWR_OFF_IGNORE_MASK", mcu.PowerReg.SOFTWARE_IGNORE_MASK),
        ("PWR_OFF_FAILED_MASK", mcu.PowerReg.FAILED_MASK),
        ("PWR_OFF_SUPPLY_COUNT", mcu.PowerReg.SUPPLY_COUNT),
        ("PWR_OFF_SUPPLY_STATES", mcu.PowerReg.SUPPLY_STATE),
    ])


def test_power_flags(defines):
    _check(defines, [
        ("PWR_FLAG_BLADE_POWER_EN", mcu.PowerFlags.BLADE_POWER_EN),
        ("PWR_FLAG_CLI_INHIBIT", mcu.PowerFlags.CLI_INHIBIT),
        ("PWR_FLAG_PROGCOM_INHIBIT", mcu.PowerFlags.PROGCOM_INHIBIT),
        ("PWR_FLAG_FAULT_LATCH", mcu.PowerFlags.POWER_FAULT_LATCH),
        ("PWR_FLAG_ALARM_SHUTDOWN_LATCH", mcu.PowerFlags.ALARM_SHUTDOWN_LATCH),
        ("PWR_FLAG_F1_ENABLE", mcu.PowerFlags.F1_ENABLE),
        ("PWR_FLAG_F2_ENABLE", mcu.PowerFlags.F2_ENABLE),
    ])


def test_alarm_page(defines):
    _check(defines, [
        ("ALM_OFF_TEMP_STATE", mcu.AlarmReg.TEMP_TASK_STATE),
        ("ALM_OFF_VOLT_STATE", mcu.AlarmReg.VOLTAGE_TASK_STATE),
        ("ALM_OFF_STATUS_T", mcu.AlarmReg.TEMP_STATUS),
        ("ALM_OFF_WARN_LATCH", mcu.AlarmReg.TEMP_WARN_LATCH),
        ("ALM_OFF_VOLT_GEN", mcu.AlarmReg.VOLTAGE_ALARM_GENERAL),
        ("ALM_OFF_VOLT_FPGA1", mcu.AlarmReg.VOLTAGE_ALARM_F1),
        ("ALM_OFF_VOLT_FPGA2", mcu.AlarmReg.VOLTAGE_ALARM_F2),
    ])


def test_adc_and_control_pages(defines):
    _check(defines, [
        ("ADC_PAGE_VALUES_OFF", mcu.AdcReg.VALUES),
        ("CTRL_OFF_COMMAND", mcu.ControlReg.COMMAND),
        ("CTRL_CMD_ASSERT_PROGCOM_POWER_INHIBIT",
         mcu.McuControlCommand.ASSERT_PROGCOM_POWER_INHIBIT),
        ("CTRL_CMD_RELEASE_PROGCOM_POWER_INHIBIT",
         mcu.McuControlCommand.RELEASE_PROGCOM_POWER_INHIBIT),
        ("CTRL_CMD_CLEAR_POWER_FAULT", mcu.McuControlCommand.CLEAR_POWER_FAULT),
        ("CTRL_CMD_CLEAR_ALARM_LATCHES", mcu.McuControlCommand.CLEAR_ALARM_LATCHES),
        ("CTRL_CMD_ZYNQMON_ENABLE_TRANSMIT",
         mcu.McuControlCommand.ZYNQMON_ENABLE_TRANSMIT),
        ("CTRL_CMD_ZYNQMON_DISABLE_TRANSMIT",
         mcu.McuControlCommand.ZYNQMON_DISABLE_TRANSMIT),
    ])


def test_capability_and_health_bits(defines):
    _check(defines, [
        ("MCU_CAP_SYSTEM", mcu.McuCapability.SYSTEM),
        ("MCU_CAP_POWER", mcu.McuCapability.POWER),
        ("MCU_CAP_ALARMS", mcu.McuCapability.ALARMS),
        ("MCU_CAP_ADC", mcu.McuCapability.ADC),
        ("MCU_CAP_PERSISTENT_LOG", mcu.McuCapability.PERSISTENT_LOG),
        ("MCU_CAP_CONTROLS", mcu.McuCapability.CONTROLS),
        ("MCU_CAP_RUNTIME", mcu.McuCapability.RUNTIME),
        ("MCU_HEALTH_POWER_FAULT", mcu.McuHealth.POWER_FAULT),
        ("MCU_HEALTH_TEMPERATURE_ALARM", mcu.McuHealth.TEMPERATURE_ALARM),
        ("MCU_HEALTH_VOLTAGE_ALARM", mcu.McuHealth.VOLTAGE_ALARM),
        ("MCU_HEALTH_ADC_ERROR", mcu.McuHealth.ADC_ERROR),
    ])


def test_capability_bit_4_stays_unassigned(defines):
    """Page 0x04 was dropped from the design; MCU_Reg.h:63-64 says bit 4 is not
    reassigned. Reusing it would make an old client misread a new page."""
    assert not any(int(c) & (1 << 4) for c in mcu.McuCapability)
    for name, text in defines.items():
        if name.startswith("MCU_CAP_"):
            assert _resolve(name, defines) != (1 << 4), "%s reuses capability bit 4" % name


def test_map_version_and_array_lengths(defines):
    assert _resolve("MCU_MAP_MAJOR", defines) == mcu.MCU_MAP_MAJOR

    # page 0x03 is one flat binary16 array; its byte length must match the
    # channel-name tuple the client decodes it with
    adc_defines = dict(defines, ADC_CHANNEL_COUNT=str(mcu.ADC_CHANNEL_COUNT))
    assert _resolve("ADC_PAGE_VALUES_LEN", adc_defines) == mcu.ADC_CHANNEL_COUNT * 2
    assert len(mcu.ADC_CHANNEL_NAMES) == mcu.ADC_CHANNEL_COUNT

    # page 0x01 supply-state array
    pwr_used = _resolve("PWR_PAGE_USED_LEN", defines)
    assert pwr_used - int(mcu.PowerReg.SUPPLY_STATE) == mcu.POWER_SUPPLY_ARRAY_LEN

    # page 0x00 git-version field
    assert _resolve("SYS_GIT_VERSION_LEN", defines) == 20

    # page 0x00 build-time field
    assert _resolve("SYS_BUILD_TIME_LEN", defines) == mcu.SYS_BUILD_TIME_LEN

    # each page's used length must leave room for its last field: a real
    # relationship rather than a literal compared with a literal
    assert _resolve("RT_PAGE_USED_LEN", defines) >= max(
        int(m) for m in mcu.RuntimeReg) + 4
    assert _resolve("SYS_PAGE_USED_LEN", defines) >= int(
        mcu.SystemReg.BUILD_TIME) + mcu.SYS_BUILD_TIME_LEN


def test_no_python_offset_crosses_a_page_boundary():
    """Device.read_reg checks reg+size <= 0xFFFF but not offset+size <= 0x100,
    so a field near the end of a page would silently bump the page byte."""
    for enum_cls in (mcu.SystemReg, mcu.PowerReg, mcu.AlarmReg,
                     mcu.ControlReg, mcu.PersistentLogInfoReg, mcu.RuntimeReg):
        for member in enum_cls:
            assert int(member) + 4 <= 0x100, (
                "%s.%s at 0x%02x leaves no room for a 4-byte read"
                % (enum_cls.__name__, member.name, int(member))
            )


_OFFSET_PREFIXES = ("SYS_OFF_", "RT_OFF_")
_ENUM_FOR_PREFIX = {
    "SYS_OFF_": mcu.SystemReg,
    "RT_OFF_": mcu.RuntimeReg,
}


def test_every_firmware_offset_has_a_python_counterpart(defines):
    """A firmware field with no client counterpart is invisible drift."""
    missing = []
    for name in defines:
        for prefix in _OFFSET_PREFIXES:
            if not name.startswith(prefix):
                continue
            enum_cls = _ENUM_FOR_PREFIX[prefix]
            value = _resolve(name, defines)
            if value not in set(int(m) for m in enum_cls):
                missing.append("%s = 0x%02x has no %s member"
                               % (name, value, enum_cls.__name__))
    assert not missing, "firmware offsets absent from the client:\n  " + \
        "\n  ".join(missing)


@pytest.mark.skipif(
    not (os.path.isfile(PROGCOM_C) and os.path.isfile(REGISTER_MAP_MD)),
    reason="ProgComTask.c or MCU_REGISTER_MAP.md not found",
)
def test_firmware_error_strings_are_documented():
    """Every 'e <text>' the MCU device can emit must appear in the register map.
    Catches the drift class where a new firmware error is never documented."""
    with open(PROGCOM_C) as handle:
        source = _strip_comments(handle.read())
    with open(REGISTER_MAP_MD) as handle:
        documented = handle.read()

    strings = set(re.findall(r'"((?:[^"\\\n]|\\.)*MCU(?:[^"\\\n]|\\.)*)"', source))
    missing = sorted(s for s in strings if s not in documented)
    assert not missing, (
        "firmware MCU error strings absent from MCU_REGISTER_MAP.md: %s" % missing
    )

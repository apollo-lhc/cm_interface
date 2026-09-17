"""Hardware-runnable checks for the LGA80D DC-DC converter.

Focused on the L09 regression: STATUS_WORD bit 6 (OFF) and bit 11
(POWER_GOOD#) must not, by themselves, make has_faults True.

Run with -s to see the printed telemetry.
"""

from cm_interface.core_config import CORE_CONFIG

# Generous, datasheet-plausible ranges (LGA80D_40A_dual_AppNote.pdf: rated
# input 7.5-14V, output 0.6-5.2V). Wide margin is intentional -- this is a
# read-succeeded/sane-value smoke check, not a calibration test.
INPUT_VOLTAGE_RANGE = (5.0, 16.0)
OUTPUT_VOLTAGE_RANGE = (0.0, 6.0)
TEMPERATURE_RANGE_C = (-40.0, 150.0)

# STATUS_WORD bits that L09 says must not by themselves indicate a fault.
_OFF_BIT = 1 << 6
_POWER_GOOD_NEGATED_BIT = 1 << 11


def test_telemetry_is_plausible(hw_registry):
    problems = []
    for name in CORE_CONFIG.lga80d:
        lga = hw_registry.get_lga80d(name)
        telemetry = lga.read_telemetry()
        print(f"{name}: {telemetry}")

        if not INPUT_VOLTAGE_RANGE[0] <= telemetry.input_voltage <= INPUT_VOLTAGE_RANGE[1]:
            problems.append(f"{name}: input_voltage={telemetry.input_voltage} out of plausible range")
        if not OUTPUT_VOLTAGE_RANGE[0] <= telemetry.output_voltage <= OUTPUT_VOLTAGE_RANGE[1]:
            problems.append(f"{name}: output_voltage={telemetry.output_voltage} out of plausible range")
        if not TEMPERATURE_RANGE_C[0] <= telemetry.temperature <= TEMPERATURE_RANGE_C[1]:
            problems.append(f"{name}: temperature={telemetry.temperature} out of plausible range")

    assert not problems, "\n".join(problems)


def test_has_faults_excludes_off_and_power_good(hw_registry):
    """Regression test for L09.

    An intentionally-off, otherwise-healthy unit must not report
    has_faults=True just because STATUS_WORD bit 6 (OFF) or bit 11
    (POWER_GOOD#) is set. We can't force a real unit into that state from
    here, so we assert the property whenever we observe it naturally, and
    always print the raw word so a human can cross-check the OFF/
    POWER_GOOD# bits against the verdict.
    """
    for name in CORE_CONFIG.lga80d:
        lga = hw_registry.get_lga80d(name)
        status = lga.read_status()
        off = bool(status.word & _OFF_BIT)
        power_good_negated = bool(status.word & _POWER_GOOD_NEGATED_BIT)
        print(
            f"{name}: word=0x{status.word:04X} off={off} "
            f"power_good_negated={power_good_negated} has_faults={status.has_faults}"
        )

        only_benign_bits_set = (status.word & ~(_OFF_BIT | _POWER_GOOD_NEGATED_BIT)) == 0
        if only_benign_bits_set:
            assert not status.has_faults, (
                f"{name}: word=0x{status.word:04X} sets only OFF/POWER_GOOD#, "
                f"but has_faults is True (L09 regression)"
            )

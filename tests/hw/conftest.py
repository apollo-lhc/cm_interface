"""Collection gate and fixtures for the hardware-runnable test suite.

These tests talk to a real UART device and real FireFly/Si5395/LGA80D
modules. They must never run as part of the ordinary software test suite
(``pytest cm_interface/tests/``), so this file uses pytest's
``collect_ignore_glob`` hook to make every ``test_*.py`` file in this
directory invisible to collection unless ``CM_INTERFACE_HW=1`` is set in the
environment -- with the gate off, pytest never even imports these modules.

See ``tests/hw/README.md`` for how to run this suite.
"""

import os

import pytest


def _hw_enabled() -> bool:
    return os.environ.get("CM_INTERFACE_HW") == "1"


def _writes_enabled() -> bool:
    return os.environ.get("CM_INTERFACE_HW_ALLOW_WRITES") == "1"


# Pytest collection hook: when the hardware gate is off, every test_*.py in
# this directory is skipped at collection time (not imported, not run, not
# reported) so it cannot interfere with a plain `pytest cm_interface/tests/`.
collect_ignore_glob = [] if _hw_enabled() else ["test_*.py"]

DEV_PATH = os.environ.get("CM_INTERFACE_DEV_PATH", "/dev/ttyUL4")
BOARD_SETUP = os.environ.get("CM_INTERFACE_BOARD_SETUP", "tf")


@pytest.fixture(scope="session")
def hw_registry():
    """A real Registry pointed at real hardware.

    Session-scoped: one physical UART connection is reused for the whole
    hardware test run, mirroring normal usage. The connection is opened
    lazily (see uart.py), so constructing this fixture does not itself
    require the device to be present -- individual register reads will
    raise/timeout if it isn't.
    """
    from cm_interface.registry import Registry

    Registry._instance = None
    return Registry(setup=BOARD_SETUP, dev_path=DEV_PATH)


@pytest.fixture(scope="session")
def allow_writes():
    """Gate for any test that writes to a register, even a safe round trip.

    Skips the test unless CM_INTERFACE_HW_ALLOW_WRITES=1 is also set, so a
    user can run the read-only identity/telemetry checks without ever
    risking a write, and enable writes only deliberately.
    """
    if not _writes_enabled():
        pytest.skip(
            "set CM_INTERFACE_HW_ALLOW_WRITES=1 to run register-write "
            "round-trip tests"
        )
    return True

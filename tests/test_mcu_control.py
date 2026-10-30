import contextlib
import io
import sys
import unittest
from unittest import mock

from cm_interface.device.mcu import McuControlCommand
from cm_interface.tools import mcu_control


class McuControlTest(unittest.TestCase):
    def test_no_action_prints_help_with_every_command(self):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["cm-mcu-control"]), \
                contextlib.redirect_stdout(out):
            self.assertEqual(mcu_control.main(), 2)
        for command in McuControlCommand:
            self.assertIn(command.name, out.getvalue())

    def test_every_command_has_help(self):
        self.assertEqual(set(mcu_control.COMMAND_HELP), set(McuControlCommand))


    def test_exit_status(self):
        class Mcu:
            def __init__(self, error=None):
                self.error = error

            def send_control(self, command):
                if self.error:
                    raise self.error

        def run(mcu, extra=("--yes",), reply="y"):
            argv = ["cm-mcu-control", "--send-control", "CLEAR_ALARM_LATCHES"] + list(extra)
            reg = type("Reg", (), {"get_mcu": lambda self: mcu})()
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(mcu_control, "make_registry", lambda args: reg), \
                    mock.patch("builtins.input", return_value=reply), \
                    contextlib.redirect_stdout(io.StringIO()):
                return mcu_control.main()

        self.assertEqual(run(Mcu()), 0)
        self.assertEqual(run(Mcu(OSError("no ack"))), 1)
        self.assertEqual(run(Mcu(), extra=(), reply="n"), 1)


if __name__ == "__main__":
    unittest.main()

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


if __name__ == "__main__":
    unittest.main()

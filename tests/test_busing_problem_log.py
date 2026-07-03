from __future__ import annotations

import builtins
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "backend"))

import BusingProblem  # noqa: E402


class BusingProblemLogTests(unittest.TestCase):
    def test_broken_stdout_pipe_does_not_break_planner_flow(self) -> None:
        BusingProblem._LOG_STDOUT_AVAILABLE = True

        with mock.patch.object(builtins, "print", side_effect=BrokenPipeError("broken")):
            BusingProblem.log("preview log")
            BusingProblem.log("second log")

        self.assertFalse(BusingProblem._LOG_STDOUT_AVAILABLE)


if __name__ == "__main__":
    unittest.main()

"""Power reporting is isolated from business batteryPct and hardware control."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apps.patchx.business import PatchXBusiness
from apps.patchx.config import validate_product
from apps.patchx.power import LinuxPowerSupply
from apps.patchx.product import PatchXCompanion
from apps.patchx.__main__ import create_runtime


class PowerProductTests(unittest.TestCase):
    def core(self, **options):
        return PatchXCompanion(validate_product({})["voice"], PatchXBusiness(), **options)

    def test_direct_product_without_reader_does_not_open_sysfs(self):
        core = self.core()
        with patch.object(Path, "open", side_effect=AssertionError("unexpected file read")):
            state = core.snapshot()
        self.assertFalse(state["patchx"]["power"]["available"])

    def test_injected_reader_supplies_only_power_snapshot(self):
        class Reader:
            calls = 0

            def snapshot(self):
                self.calls += 1
                return {"available": True, "battery": {"capacity_pct": 37}, "errors": {}}

        reader = Reader()
        core = self.core(power_supply=reader)
        self.assertEqual(reader.calls, 0)
        self.assertEqual(core.snapshot()["patchx"]["power"]["battery"]["capacity_pct"], 37)
        self.assertEqual(reader.calls, 1)
        self.assertEqual(core.robot_status()["payload"]["batteryPct"], 0)

    def test_empty_sysfs_reports_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            core = self.core(power_supply=LinuxPowerSupply(directory))
            power = core.snapshot()["patchx"]["power"]
        self.assertFalse(power["available"])
        self.assertIsNone(power["battery"]["capacity_pct"])
        self.assertIn("battery", power["errors"])

    def test_runtime_assembles_read_only_reader(self):
        with patch("apps.patchx.__main__.LinuxPowerSupply") as factory:
            factory.return_value.snapshot.return_value = {"available": False, "errors": {}}
            runtime = create_runtime({}, http_address=("127.0.0.1", 0))
            try:
                factory.assert_called_once_with()
                factory.return_value.snapshot.assert_not_called()
                self.assertFalse(runtime.core.snapshot()["patchx"]["power"]["available"])
                factory.return_value.snapshot.assert_called_once_with()
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()

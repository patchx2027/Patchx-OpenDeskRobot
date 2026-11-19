"""Read-only power-supply fixtures; no physical devices are opened."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apps.patchx.power import LinuxPowerSupply


class PowerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.reader = LinuxPowerSupply(self.root)

    def supply(self, name, **fields):
        path = self.root / name
        path.mkdir(exist_ok=True)
        for key, value in fields.items():
            (path / key).write_text(str(value) + "\n", encoding="ascii")

    def battery(self, **changes):
        fields = dict(type="Battery", present=1, capacity=37, voltage_now=7600000,
                      current_now=-272000, temp=188, status="Charging", health="Good")
        fields.update(changes)
        self.supply("battery", **fields)

    def test_real_units_and_independent_charger(self):
        self.battery()
        self.supply("bq25700-charger", type="USB", online=1, status="Charging", health="Good")
        result = self.reader.snapshot()
        self.assertTrue(result["available"])
        self.assertEqual(result["errors"], {})
        battery = result["battery"]
        self.assertEqual(battery["capacity_pct"], 37)
        self.assertEqual(battery["raw"]["voltage_now"], 7600000)
        self.assertEqual(battery["voltage_v"], 7.6)
        self.assertEqual(battery["current_a"], -.272)
        self.assertEqual(battery["temperature_c"], 18.8)
        self.assertTrue(battery["charging"])
        self.assertTrue(result["charger"]["online"])
        self.assertGreater(result["sampled_monotonic"], 0)

    def test_missing_supplies_never_report_zero_capacity(self):
        result = self.reader.snapshot()
        self.assertFalse(result["available"])
        self.assertIsNone(result["battery"]["capacity_pct"])
        self.assertIsNone(result["battery"]["charging"])
        self.assertFalse(result["charger"]["available"])
        self.assertIn("battery", result["errors"])

    def test_zero_capacity_is_valid_and_charger_optional(self):
        self.battery(capacity=0, status="Discharging")
        result = self.reader.snapshot()
        self.assertTrue(result["available"])
        self.assertEqual(result["battery"]["capacity_pct"], 0)
        self.assertFalse(result["battery"]["charging"])
        self.assertIn("charger", result["errors"])

    def test_absent_battery_discards_capacity(self):
        self.battery(present=0)
        result = self.reader.snapshot()
        self.assertFalse(result["available"])
        self.assertIsNone(result["battery"]["capacity_pct"])
        self.assertFalse(result["battery"]["present"])

    def test_malformed_fields_are_isolated_and_do_not_reuse_old_sample(self):
        self.battery()
        before = self.reader.snapshot()
        self.battery(capacity=101, current_now="nan", temp="9" * 100,
                     voltage_now="7.6", status="invented")
        after = self.reader.snapshot()
        self.assertFalse(after["available"])
        self.assertIsNone(after["battery"]["capacity_pct"])
        self.assertIsNone(after["battery"]["voltage_v"])
        self.assertIsNone(after["battery"]["current_a"])
        self.assertIsNone(after["battery"]["temperature_c"])
        self.assertIsNone(after["battery"]["charging"])
        self.assertGreaterEqual(after["sampled_monotonic"], before["sampled_monotonic"])
        self.assertIn("battery.capacity", after["errors"])

    def test_partial_read_preserves_valid_capacity_without_fabricating_temperature(self):
        self.battery()
        (self.root / "battery/temp").unlink()
        result = self.reader.snapshot()
        self.assertTrue(result["available"])
        self.assertEqual(result["battery"]["capacity_pct"], 37)
        self.assertIsNone(result["battery"]["temperature_c"])
        self.assertIn("battery.temp", result["errors"])

    def test_unknown_status_does_not_use_current_sign(self):
        self.battery(status="Unknown", current_now=100000)
        result = self.reader.snapshot()
        self.assertIsNone(result["battery"]["charging"])

    def test_rk817_without_present_attribute_keeps_real_capacity(self):
        self.battery()
        (self.root / "battery/present").unlink()
        result = self.reader.snapshot()
        self.assertTrue(result["available"])
        self.assertIsNone(result["battery"]["present"])
        self.assertEqual(result["battery"]["capacity_pct"], 37)
        self.assertNotIn("battery.present", result["errors"])

    def test_invalid_present_flag_invalidates_capacity(self):
        self.battery(present=2)
        result = self.reader.snapshot()
        self.assertFalse(result["available"])
        self.assertIsNone(result["battery"]["capacity_pct"])
        self.assertIn("battery.present", result["errors"])

    def test_reader_does_not_change_files(self):
        self.battery()
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.reader.snapshot()
        self.reader.snapshot()
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_rejects_relative_roots_and_path_traversal(self):
        with self.assertRaises(ValueError):
            LinuxPowerSupply("relative")
        with self.assertRaises(ValueError):
            LinuxPowerSupply(self.root, battery_name="../battery")


if __name__ == "__main__":
    unittest.main()

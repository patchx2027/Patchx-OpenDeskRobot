#!/usr/bin/env python3
"""Source lifecycle guards, not proof of runtime or concurrent correctness."""
import pathlib
import re
import unittest


SOURCE = pathlib.Path(__file__).resolve().parents[4] / "drivers/power/supply/rk817_battery.c"


def function(source, name):
    definition = re.search(r"\b" + re.escape(name) + r"\([^;{}]*\)\n\{", source)
    if definition is None:
        raise AssertionError("missing definition: " + name)
    start = definition.end()
    depth = 1
    end = start
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end - 1]


class LifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text()

    def test_child_owns_data_and_irq_failure_is_returned(self):
        probe = function(self.source, "rk817_battery_probe")
        self.assertIn("devm_kzalloc(&pdev->dev", probe)
        self.assertIn("battery->pdev = pdev", probe)
        self.assertIn("ret = rk809_charge_init_irqs(battery)", probe)
        self.assertIn("if (ret)\n\t\t\treturn ret;", probe)

    def test_cleanup_registration_and_async_start_order(self):
        probe = function(self.source, "rk817_battery_probe")
        ordered = ["alloc_ordered_workqueue", "if (!battery->bat_monitor_wq)",
                   "rk817_bat_destroy_workqueue, battery", "wake_lock_init",
                   "rk817_bat_destroy_wake_lock, battery", "rk817_bat_init_fg(battery)",
                   "rk817_bat_init_power_supply(battery)",
                   "rk817_bat_quiesce, battery", "rk809_charge_init_irqs(battery)",
                   "mod_timer", "queue_delayed_work"]
        positions = [probe.index(value) for value in ordered]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("add_timer", function(self.source, "rk817_bat_init_caltimer"))

    def test_shutdown_and_devres_share_quiesce(self):
        self.assertIn("rk817_bat_quiesce(platform_get_drvdata(dev))",
                      function(self.source, "rk817_battery_shutdown"))
        cleanup = function(self.source, "rk817_bat_quiesce")
        ordered = ["WRITE_ONCE(battery->stopping, true)", "del_timer_sync",
                   "cancel_work_sync(&battery->resume_work)",
                   "cancel_delayed_work_sync(&battery->calib_delay_work)",
                   "cancel_delayed_work_sync(&battery->bat_delay_work)"]
        positions = [cleanup.index(value) for value in ordered]
        self.assertEqual(positions, sorted(positions))

    def test_async_producers_check_stopping(self):
        for name in ("rk817_bat_caltimer_isr", "rk817_battery_work",
                     "rk817_bat_resume_work", "rk817_bat_pm_resume"):
            body = function(self.source, name)
            self.assertIn("READ_ONCE(battery->stopping)", body, name)
            for call in ("queue_work(", "queue_delayed_work(", "mod_timer("):
                if call in body:
                    self.assertLess(body.index("READ_ONCE(battery->stopping)"), body.index(call), name)


if __name__ == "__main__":
    unittest.main()

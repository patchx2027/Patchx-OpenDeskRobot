#!/usr/bin/env python3
"""Execute actual driver OCV functions with fake hardware inputs on the host.

This tests arithmetic and branch behavior, not PMIC I/O or battery calibration.
"""
import pathlib
import re
import subprocess
import tempfile
import unittest


SOURCE = pathlib.Path(__file__).resolve().parents[4] / "drivers/power/supply/rk817_battery.c"


def definition(source, name):
    match = re.search(r"static (?:int|void) " + name + r"\([^;{}]*\)\n\{", source)
    if not match:
        raise AssertionError("missing function: " + name)
    depth, end = 1, match.end()
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end]


PREFIX = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdlib.h>
#define BAT_INFO(...) do {} while (0)
#define DBG(...) do {} while (0)
struct battery_platform_data { int max_soc_offset; };
struct rk817_battery_device {
    struct battery_platform_data *pdata;
    int fcc, remain_cap, halt_cnt, pwroff_min, rsoc, dsoc, nac;
    int ocv_pre_dsoc, ocv_new_dsoc, force_pre_dsoc, force_new_dsoc;
    bool is_halt, is_initialized, is_ocv_calib, is_max_soc_offset, is_force_calib;
};
static int saved_soc, saved_cap, ocv_percent;
static bool halted, initialized, valid_ocv;
static int rk817_bat_get_fcc(struct rk817_battery_device *b) { (void)b; return 3500; }
static int rk817_bat_get_prev_dsoc(struct rk817_battery_device *b) { (void)b; return saved_soc; }
static int rk817_bat_get_prev_cap(struct rk817_battery_device *b) { (void)b; return saved_cap; }
static int rk817_bat_get_capacity_mah(struct rk817_battery_device *b) { (void)b; return 123; }
static bool is_rk817_bat_last_halt(struct rk817_battery_device *b) { (void)b; return halted; }
static int rk817_bat_get_halt_cnt(struct rk817_battery_device *b) { (void)b; return 0; }
static bool is_rk817_bat_initialized(struct rk817_battery_device *b) { (void)b; return initialized; }
static bool is_rk817_bat_ocv_valid(struct rk817_battery_device *b) { (void)b; return valid_ocv; }
static int rk817_bat_get_ocv_voltage(struct rk817_battery_device *b) { (void)b; return 8142; }
static int rk817_bat_vol_to_soc(struct rk817_battery_device *b, int v) {
    (void)b; (void)v; return ocv_percent;
}
static int rk817_bat_vol_to_cap(struct rk817_battery_device *b, int v) {
    (void)b; (void)v; return 3220;
}
static void rk817_bat_init_coulomb_cap(struct rk817_battery_device *b, unsigned int cap) {
    (void)cap; b->rsoc = 4000;
}
'''

HARNESS = r'''
static struct rk817_battery_device sample(int previous, int percent, bool valid, int off) {
    static struct battery_platform_data pdata = { .max_soc_offset = 60 };
    struct rk817_battery_device b = { .pdata = &pdata, .pwroff_min = off };
    saved_soc = previous; saved_cap = 111; ocv_percent = percent;
    valid_ocv = valid;
    rk817_bat_not_first_pwron(&b);
    return b;
}
int main(void) {
    struct rk817_battery_device b;
    assert(rk817_bat_percent_to_dsoc(0) == 0);
    assert(rk817_bat_percent_to_dsoc(1) == 1000);
    assert(rk817_bat_percent_to_dsoc(92) == 92000);
    assert(rk817_bat_percent_to_dsoc(100) == 100000);
    /* Reproduce the board observation: 11022 -> 92 percent, not dsoc 92. */
    b = sample(11022, 92, true, 1);
    assert(b.dsoc == 92000 && b.nac == 3220 && b.is_max_soc_offset);
    assert(b.ocv_pre_dsoc == 11022 && b.ocv_new_dsoc == 92000);
    assert((b.dsoc + 500) / 1000 == 92);
    b = sample(32001, 92, true, 1);
    assert(b.dsoc == 32001 && !b.is_max_soc_offset && b.nac == 3220);
    b = sample(32000, 92, true, 1);
    assert(b.dsoc == 92000 && b.is_max_soc_offset);
    b = sample(92000, 92, true, 1);
    assert(b.dsoc == 92000 && !b.is_max_soc_offset);
    b = sample(12000, 92, false, 1);
    assert(b.dsoc == 92000 && b.nac == 3220 && b.is_force_calib);
    b = sample(12001, 92, false, 1);
    assert(b.dsoc == 12001 && b.nac == 111 && !b.is_force_calib);
    b = sample(0, 100, true, 1);
    assert(b.dsoc == 100000);
    b = sample(60000, 0, true, 1);
    assert(b.dsoc == 0);
    initialized = true;
    b = sample(11022, 92, true, 1);
    assert(b.dsoc == 11022 && b.nac == 111 && !b.is_max_soc_offset);
    initialized = false;
    b = sample(11022, 92, false, 0);
    assert(b.dsoc == 11022 && b.nac == 111 && !b.is_force_calib);
    halted = true;
    b = sample(11022, 92, true, 1);
    assert(b.dsoc == 4000 && b.nac == 123 && !b.is_max_soc_offset);
    return 0;
}
'''


class OcvTests(unittest.TestCase):
    def test_actual_c_functions_and_natural_calibration_branches(self):
        source = SOURCE.read_text()
        helper = definition(source, "rk817_bat_percent_to_dsoc")
        calibration = definition(source, "rk817_bat_not_first_pwron")
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory)
            (path / "ocv.c").write_text(PREFIX + helper + calibration + HARNESS)
            subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                            "-fsanitize=undefined", "-fno-sanitize-recover=all",
                            str(path / "ocv.c"), "-o", str(path / "ocv")], check=True)
            subprocess.run([str(path / "ocv")], check=True)

    def test_both_ocv_assignments_use_the_conversion(self):
        body = definition(SOURCE.read_text(), "rk817_bat_not_first_pwron")
        calls = re.findall(r"ocv_soc\s*=\s*rk817_bat_percent_to_dsoc\(\s*"
                           r"rk817_bat_vol_to_soc\(battery, ocv_vol\)\s*\);", body)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()

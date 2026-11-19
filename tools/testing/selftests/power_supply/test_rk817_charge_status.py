#!/usr/bin/env python3
"""Execute the driver's charge-status classifier, without PMIC hardware."""
import pathlib
import subprocess
import tempfile
import unittest

from test_rk817_ocv import SOURCE, definition


HARNESS = r'''
#include <assert.h>
#include <stdbool.h>
#define RK809_ID 0x8090
#define CHG_STS 0
#define DBG(...) do {} while (0)
enum { CHRG_OFF, DEAD_CHRG, TRICKLE_CHRG, CC_OR_CV_CHRG, CHARGE_FINISH,
       USB_OVER_VOL, BAT_TMP_ERR, BAT_TIM_ERR };
struct battery_platform_data { unsigned int design_max_voltage; };
struct rk817_battery_device {
    struct battery_platform_data *pdata;
    int chip_id, voltage_avg, current_avg, rsoc, plugin_trigger;
};
static int hardware_status, hardware_reads;
static int rk817_bat_field_read(struct rk817_battery_device *b, int field) {
    (void)b; assert(field == CHG_STS); hardware_reads++; return hardware_status;
}
'''

CASES = r'''
int main(void) {
    struct battery_platform_data p = { .design_max_voltage = 0 };
    struct rk817_battery_device b = {
        .pdata = &p, .chip_id = RK809_ID, .voltage_avg = 8088,
        .current_avg = 225, .rsoc = 92000, .plugin_trigger = 1
    };
    /* Board observation: low positive net current, no validated threshold. */
    assert(rk817_bat_get_charge_status(&b) == CC_OR_CV_CHRG);
    b.rsoc = 100000;
    assert(rk817_bat_get_charge_status(&b) == CC_OR_CV_CHRG);
    b.rsoc = 92000;
    b.plugin_trigger = 0;
    assert(rk817_bat_get_charge_status(&b) == CHRG_OFF);
    p.design_max_voltage = 8000;
    assert(rk817_bat_get_charge_status(&b) == CHRG_OFF);
    b.plugin_trigger = 1;
    b.voltage_avg = 8000;
    assert(rk817_bat_get_charge_status(&b) == CC_OR_CV_CHRG);
    b.voltage_avg = 8001;
    assert(rk817_bat_get_charge_status(&b) == CHARGE_FINISH);
    b.current_avg = 0;
    assert(rk817_bat_get_charge_status(&b) == CC_OR_CV_CHRG);
    b.current_avg = -1;
    assert(rk817_bat_get_charge_status(&b) == CC_OR_CV_CHRG);
    b.current_avg = 499;
    assert(rk817_bat_get_charge_status(&b) == CHARGE_FINISH);
    b.current_avg = 500;
    assert(rk817_bat_get_charge_status(&b) == CC_OR_CV_CHRG);
    b.current_avg = 1000;
    b.rsoc = 100000;
    assert(rk817_bat_get_charge_status(&b) == CHARGE_FINISH);
    b.plugin_trigger = 0;
    assert(rk817_bat_get_charge_status(&b) == CHRG_OFF);
    assert(hardware_reads == 0);
    /* RK817 continues using hardware status and its existing full-SOC rule. */
    b.chip_id = 0x8170;
    for (int value = CHRG_OFF; value <= BAT_TIM_ERR; value++) {
        hardware_status = value;
        b.rsoc = 99000;
        assert(rk817_bat_get_charge_status(&b) == value);
        b.rsoc = 100000;
        assert(rk817_bat_get_charge_status(&b) ==
               (value == CC_OR_CV_CHRG ? CHARGE_FINISH : value));
    }
    assert(hardware_reads == 16);
    return 0;
}
'''


class ChargeStatusTests(unittest.TestCase):
    def test_actual_c_classifier(self):
        classifier = definition(SOURCE.read_text(), "rk817_bat_get_charge_status")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "status.c").write_text(HARNESS + classifier + CASES)
            subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-sign-compare",
                            "-fsanitize=undefined", "-fno-sanitize-recover=all",
                            str(root / "status.c"), "-o", str(root / "status")], check=True)
            subprocess.run([str(root / "status")], check=True)


if __name__ == "__main__":
    unittest.main()

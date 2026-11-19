#!/usr/bin/env python3
"""Run actual legacy BQ25700 PD callbacks with host power-supply/I/O mocks.

These checks cover callback value handling, not device identity, register
compatibility, PD negotiation, or charger lifecycle. No device is accessed.
"""
import pathlib
import subprocess
import tempfile
import unittest
from test_rk817_ocv import definition

SOURCE = pathlib.Path(__file__).resolve().parents[4] / "drivers/power/supply/bq25700_charger.c"

PREFIX = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
#define PSY_EVENT_PROP_CHANGED 1
#define NOTIFY_OK 0
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define IS_ERR_OR_NULL(p) (!(p))
#define dev_info(...) do {} while (0)
#define dev_err(...) do {} while (0)
#define INIT_DELAYED_WORK(p,f) do { (void)(p); (void)(f); } while (0)
#define msecs_to_jiffies(x) (x)
enum { POWER_SUPPLY_PROP_ONLINE, POWER_SUPPLY_PROP_CURRENT_NOW,
 POWER_SUPPLY_PROP_VOLTAGE_NOW, POWER_SUPPLY_PROP_CURRENT_MAX,
 POWER_SUPPLY_PROP_VOLTAGE_MAX, PROP_COUNT };
enum { INPUT_CURRENT, INPUT_VOLTAGE, CHARGE_CURRENT };
enum { TBL_INPUTCUR, TBL_INPUTVOL, TBL_ICHG };
union power_supply_propval { int intval; };
struct device { struct device *parent; void *of_node; };
struct power_supply_desc { const char *name; };
struct power_supply { struct device dev; struct power_supply_desc *desc; };
struct notifier_block { int (*notifier_call)(struct notifier_block *,unsigned long,void *); };
struct bq25700_state { int unused; };
struct bq25700_device {
 struct device *dev;
 struct notifier_block nb;
 void *notify_node, *usb_charger_wq, *supply_charger;
 struct { const char *notify_device; } plat_data;
 struct { unsigned int ichg; } init_data;
 struct bq25700_state state;
 int discnt_work, automode;
};
static int values[PROP_COUNT], fail_prop, write_fail, writes, changes, queued;
static int fields[3], written[3], state_fail;
static struct power_supply *provider;
static int power_supply_get_property(struct power_supply *p,int key,union power_supply_propval *v) {
 assert(p == provider);
 if (key == fail_prop) return -EIO;
 v->intval=values[key]; return 0;
}
/* Conversion is a boundary mock: assert callback selects values in correct units. */
static int bq25700_find_idx(unsigned int value,int table) {
 if (table==TBL_INPUTCUR) return value>6350000 ? 127 : value/50000;
 if (table==TBL_INPUTVOL) return value<3200000 ? 0 :
  value>19520000 ? 255 : (value-3200000)/64000;
 assert(table==TBL_ICHG); return value>8128000 ? 127 : value/64000;
}
static int bq25700_field_write(struct bq25700_device *b,int field,unsigned int value) {
 (void)b; assert(writes<3); fields[writes]=field; written[writes]=value; writes++;
 return writes==write_fail ? -EIO : 0;
}
static int bq25700_get_chip_state(struct bq25700_device *b,struct bq25700_state *s) {
 (void)b; if (state_fail) return -EIO; s->unused=0; return 0;
}
static void power_supply_changed(void *p) { (void)p; changes++; }
static void queue_delayed_work(void *q,int *w,int delay) { (void)q;(void)w;(void)delay;queued++; }
static void bq25700_discnt_evt_worker(void) {}
static int power_supply_reg_notifier(struct notifier_block *nb) { (void)nb; return 0; }
static struct power_supply *power_supply_get_by_phandle(void *n,const char *key) {
 (void)n;(void)key;return provider;
}
static struct power_supply *power_supply_get_by_name(const char *name) { (void)name;return provider; }
'''

CASES = r'''
static void reset(void) {
 writes=changes=queued=0;fail_prop=-1;write_fail=state_fail=0;
 memset(values,0,sizeof(values));
 values[POWER_SUPPLY_PROP_ONLINE]=1;
 values[POWER_SUPPLY_PROP_CURRENT_NOW]=2000000;
 values[POWER_SUPPLY_PROP_VOLTAGE_NOW]=15000000;
 values[POWER_SUPPLY_PROP_CURRENT_MAX]=2000000;
 values[POWER_SUPPLY_PROP_VOLTAGE_MAX]=15000000;
}
int main(void) {
 struct device parent={0}, dev={.of_node=(void *)1};
 struct power_supply_desc desc={.name="tcpm"};
 struct power_supply psy={.dev={.parent=&parent},.desc=&desc};
 struct bq25700_device b={.dev=&dev,.plat_data={.notify_device="tcpm"},.init_data={.ichg=23}};
 provider=&psy;
 reset();assert(bq25700_register_pd_nb(&b)==0);
 assert(writes==3 && fields[0]==INPUT_CURRENT && written[0]==40);
 assert(fields[2]==CHARGE_CURRENT && written[2]==23);
 reset();bq2570x_pd_notifier_call(&b.nb,PSY_EVENT_PROP_CHANGED,&psy);
 assert(writes==3 && written[0]==40 && written[2]==23 && changes==1);
 for (int i=POWER_SUPPLY_PROP_CURRENT_MAX;i<=POWER_SUPPLY_PROP_VOLTAGE_MAX;i++) {
  reset();fail_prop=i;assert(bq25700_register_pd_nb(&b)==-EIO);assert(writes==0);
 }
 for (int i=POWER_SUPPLY_PROP_ONLINE;i<=POWER_SUPPLY_PROP_VOLTAGE_NOW;i++) {
  reset();fail_prop=i;bq2570x_pd_notifier_call(&b.nb,PSY_EVENT_PROP_CHANGED,&psy);assert(writes==0);
 }
 for (int n=-1;n<=0;n++) {
  reset();values[POWER_SUPPLY_PROP_CURRENT_NOW]=n;
  bq2570x_pd_notifier_call(&b.nb,PSY_EVENT_PROP_CHANGED,&psy);assert(writes==0);
  reset();values[POWER_SUPPLY_PROP_CURRENT_MAX]=n;
  assert(bq25700_register_pd_nb(&b)==-EINVAL);assert(writes==0);
  reset();values[POWER_SUPPLY_PROP_VOLTAGE_NOW]=n;
  bq2570x_pd_notifier_call(&b.nb,PSY_EVENT_PROP_CHANGED,&psy);assert(writes==0);
  reset();values[POWER_SUPPLY_PROP_VOLTAGE_MAX]=n;
  assert(bq25700_register_pd_nb(&b)==-EINVAL);assert(writes==0);
 }
 /* The existing voltage-margin formula must never wrap negative to u32. */
 reset();values[POWER_SUPPLY_PROP_VOLTAGE_NOW]=4479999;
 bq2570x_pd_notifier_call(&b.nb,PSY_EVENT_PROP_CHANGED,&psy);assert(writes==0);
 reset();values[POWER_SUPPLY_PROP_VOLTAGE_MAX]=4479999;
 assert(bq25700_register_pd_nb(&b)==-EINVAL);assert(writes==0);
 reset();values[POWER_SUPPLY_PROP_VOLTAGE_NOW]=4480000;
 bq2570x_pd_notifier_call(&b.nb,PSY_EVENT_PROP_CHANGED,&psy);
 assert(writes==3 && fields[1]==INPUT_VOLTAGE && written[1]==0);
 reset();values[POWER_SUPPLY_PROP_VOLTAGE_MAX]=4480000;
 assert(bq25700_register_pd_nb(&b)==0);
 assert(writes==3 && fields[1]==INPUT_VOLTAGE && written[1]==0);
 for (int v=5000000;v<=15000000;v+=5000000) {
  reset();values[POWER_SUPPLY_PROP_VOLTAGE_NOW]=v;
  bq2570x_pd_notifier_call(&b.nb,PSY_EVENT_PROP_CHANGED,&psy);
  assert(writes==3 && written[0]==40 && written[2]==23);
  reset();values[POWER_SUPPLY_PROP_VOLTAGE_MAX]=v;
  assert(bq25700_register_pd_nb(&b)==0);
  assert(writes==3 && written[0]==40 && written[2]==23);
 }
 for (int f=1;f<=3;f++) {
  reset();write_fail=f;assert(bq25700_register_pd_nb(&b)==-EIO);assert(writes==f);
  reset();write_fail=f;bq2570x_pd_notifier_call(&b.nb,PSY_EVENT_PROP_CHANGED,&psy);
  assert(writes==f && changes==0);
 }
 reset();state_fail=1;b.state.unused=42;
 bq2570x_pd_notifier_call(&b.nb,PSY_EVENT_PROP_CHANGED,&psy);
 assert(writes==3 && changes==0 && b.state.unused==42);
 reset();values[POWER_SUPPLY_PROP_ONLINE]=0;
 bq2570x_pd_notifier_call(&b.nb,PSY_EVENT_PROP_CHANGED,&psy);assert(writes==0 && queued==1);
 reset();desc.name="other";
 bq2570x_pd_notifier_call(&b.nb,PSY_EVENT_PROP_CHANGED,&psy);assert(writes==0);
 desc.name="tcpm";
 reset();bq2570x_pd_notifier_call(&b.nb,0,&psy);assert(writes==0);
 return 0;
}
'''

class PDCallbackTests(unittest.TestCase):
    def test_actual_callbacks(self):
        source=SOURCE.read_text()
        callbacks=definition(source,"bq2570x_pd_notifier_call")+definition(source,"bq25700_register_pd_nb")
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory)
            (root/"pd.c").write_text(PREFIX+callbacks+CASES)
            subprocess.run(["cc","-std=c11","-Wall","-Wextra","-Werror","-Wno-sign-compare",
                            "-fsanitize=undefined","-fno-sanitize-recover=all",
                            str(root/"pd.c"),"-o",str(root/"pd")],check=True)
            subprocess.run([str(root/"pd")],check=True)

if __name__=="__main__":
    unittest.main()

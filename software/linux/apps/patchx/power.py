"""Read-only Linux power_supply sampling for the non-real-time product thread.

No device discovery or writes are performed. Supply names are explicit because
an unrelated USB peripheral's battery must not become the robot's battery.
Reads are sequential, not an atomic hardware snapshot. Current retains the
driver's sign convention; charging is derived only from the status property.
"""
from pathlib import Path
import re
import time


_STATUSES = {"Unknown", "Charging", "Discharging", "Not charging", "Full"}


class LinuxPowerSupply:
    def __init__(self, root="/sys/class/power_supply", battery_name="battery",
                 charger_name="bq25700-charger"):
        self.root = Path(root)
        if not self.root.is_absolute():
            raise ValueError("power_supply root must be absolute")
        for name in (battery_name, charger_name):
            if (not isinstance(name, str) or not name or name in (".", "..") or
                    "/" in name or "\\" in name or "\x00" in name):
                raise ValueError("power_supply name must be a single path component")
        self.battery_name, self.charger_name = battery_name, charger_name

    def _read(self, name, field, errors, label, *, optional=False, integer=False,
              minimum=-(2 ** 63), maximum=2 ** 63 - 1, choices=None):
        try:
            with (self.root / name / field).open("r", encoding="ascii") as stream:
                value = stream.read(129)
            if len(value) > 128:
                raise ValueError("oversized property")
            value = value.strip()
            if not value or "\x00" in value:
                raise ValueError("invalid property")
            if integer:
                if not re.fullmatch(r"-?[0-9]{1,20}", value):
                    raise ValueError("invalid integer")
                value = int(value)
                if not minimum <= value <= maximum:
                    raise ValueError("out of range")
            elif choices is not None and value not in choices:
                raise ValueError("unknown property value")
            return value
        except FileNotFoundError:
            if not optional:
                errors[label + "." + field] = "missing"
        except (OSError, ValueError, UnicodeError):
            errors[label + "." + field] = "read failed or invalid value"
        return None

    def _sample(self, name, label, errors, *, battery):
        result = {"name": name, "available": False, "raw": {}, "status": None,
                  "charging": None, "health": None}
        if battery:
            result.update(present=None, capacity_pct=None, voltage_v=None,
                          current_a=None, temperature_c=None)
        else:
            result["online"] = None
        try:
            exists = (self.root / name).is_dir()
        except OSError:
            exists = False
        if not exists:
            errors[label] = "power supply unavailable"
            return result

        def read(field, **options):
            value = self._read(name, field, errors, label, **options)
            result["raw"][field] = value
            return value

        kind = read("type")
        if battery and kind != "Battery":
            errors[label + ".type"] = "expected Battery supply"
            return result
        result["status"] = read("status", choices=_STATUSES)
        result["health"] = read("health", optional=True)
        if result["status"] is not None and result["status"] != "Unknown":
            result["charging"] = result["status"] == "Charging"
        if battery:
            # Some RK817 drivers do not expose PRESENT. An explicit zero still
            # invalidates capacity; a missing property does not invent presence.
            present = read("present", optional=True, integer=True, minimum=0, maximum=1)
            result["present"] = bool(present) if present is not None else None
            capacity = read("capacity", integer=True, minimum=0, maximum=100)
            result["capacity_pct"] = (capacity if present != 0 and
                                      label + ".present" not in errors else None)
            for field, converted, divisor, minimum in (
                    ("voltage_now", "voltage_v", 1000000, 0),
                    ("current_now", "current_a", 1000000, -(2 ** 63)),
                    ("temp", "temperature_c", 10, -2731)):
                value = read(field, integer=True, minimum=minimum)
                result[converted] = value / divisor if value is not None else None
            result["available"] = result["capacity_pct"] is not None
        else:
            online = read("online", integer=True, minimum=0, maximum=1)
            result["online"] = bool(online) if online is not None else None
            result["available"] = kind is not None and online is not None
        return result

    def snapshot(self):
        """Return a fresh sample; missing/invalid measurements remain None.

        available means battery capacity is usable, independently of charger
        availability. errors reports partial failures even when available=true.
        raw uses Linux property names/units: capacity %, voltage_now microvolts,
        current_now microamps, temp tenths of a degree Celsius. The timestamp
        marks the end of this read operation, not the hardware conversion time.
        """
        errors = {}
        battery = self._sample(self.battery_name, "battery", errors, battery=True)
        charger = self._sample(self.charger_name, "charger", errors, battery=False)
        return {"available": battery["available"], "errors": errors,
                "sampled_monotonic": time.monotonic(), "battery": battery, "charger": charger}

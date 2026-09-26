"""Built-in current database for common parts, plus the user's own additions.

Every entry describes a part by behaviour rather than by application:

* ``supply``     - current the part itself draws, charged to the first connected
                   pin of each group (e.g. a module powered from VIN *or* +5V).
* ``regulators`` - converters: input current follows the load on the output pins
                   (``linear``: I_in = I_out; ``buck``: I_in = I_out*Vout/(Vin*eff)).
* ``drivers``    - switches/bridges: supply current is the sum of the loads on the
                   output channels, checked against the channel limits.

Built-in values come from the manufacturers' datasheets (each entry cites the URL
and page). The few whose datasheet could not be retrieved are marked
``verified: false`` and the analysis reports them as unverified.
Parts confirmed against a datasheet with ``set_part_current`` are stored in
``~/.kicad10_mcp/parts.json`` with ``verified: true`` and a source, and override
the built-ins.
"""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any, Optional

USER_DB = Path.home() / ".kicad10_mcp" / "parts.json"

_UNVERIFIED = {"verified": False, "source": "built-in estimate; datasheet could not be retrieved - verify"}

# Values below were read from the manufacturers' datasheets (source + page on each
# entry) unless the entry says verified: False. Where a datasheet gives only a
# typical figure, max_a repeats it and the note says so.
BUILTIN: dict[str, dict[str, Any]] = {
    # ---------------------------------------------------------- regulators
    "AMS1117": {
        "match": ["AMS1117*", "LM1117*", "LD1117*"],
        "supply": [{"pins": ["VI", "IN", "VIN"], "typ_a": 0.005, "max_a": 0.011}],
        "regulators": [{"in_pins": ["VI", "IN", "VIN"], "out_pins": ["VO", "OUT", "VOUT"],
                        "type": "linear", "max_out_a": 1.0,
                        "note": "current limit 0.9 min / 1.1 typ / 1.5 max A"}],
        "verified": True,
        "source": "http://www.advanced-monolithic.com/pdf/ds1117.pdf p.1 (1 A output), p.3 (quiescent 5/11 mA, current limit)",
    },
    "AP2112K": {
        "match": ["AP2112*"],
        "supply": [{"pins": ["VIN"], "typ_a": 0.000055, "max_a": 0.00008}],
        "regulators": [{"in_pins": ["VIN"], "out_pins": ["VOUT"], "type": "linear",
                        "max_out_a": 0.6}],
        "verified": True,
        "source": "https://www.diodes.com/assets/Datasheets/AP2112.pdf p.8 (AP2112-3.3: IQ 55/80 uA, IOUT(MAX) 600 mA min)",
    },
    "MCP1700": {
        "match": ["MCP1700*"],
        "supply": [{"pins": ["VI", "VIN"], "typ_a": 0.0000016, "max_a": 0.000004}],
        "regulators": [{"in_pins": ["VI", "VIN"], "out_pins": ["VO", "VOUT"], "type": "linear",
                        "max_out_a": 0.25}],
    },
    "L78xx": {
        "match": ["L78*", "LM78*", "MC78*", "UA78*"],
        "supply": [{"pins": ["IN", "VI"], "typ_a": 0.005, "max_a": 0.008}],
        "regulators": [{"in_pins": ["IN", "VI"], "out_pins": ["OUT", "VO"], "type": "linear",
                        "max_out_a": 1.5}],
    },
    "LM2596": {
        "match": ["LM2596*"],
        "supply": [{"pins": ["VIN"], "typ_a": 0.005, "max_a": 0.010}],
        "regulators": [{"in_pins": ["VIN"], "out_pins": ["OUT"], "type": "buck",
                        "efficiency": 0.73, "max_out_a": 3.0,
                        "note": "efficiency 73% (3.3 V), 80% (5 V), 90% (12 V) at 3 A; lowest used"}],
        "verified": True,
        "source": "https://www.ti.com/lit/ds/symlink/lm2596.pdf p.1 (3 A), p.6 (efficiency), p.7 (IQ 5/10 mA)",
    },
    # ------------------------------------------------------ MCU / modules
    "Arduino_Nano": {
        "match": ["Arduino_Nano_v2.x", "Arduino_Nano_v3.x"],
        "supply": [{"pins": ["VIN", "+5V"], "typ_a": 0.0454, "max_a": 0.0454,
                    "note": "ATmega328P 8 mA + 4 LEDs x 5.6 mA (max, power tree) + FT232RL 15 mA (typ); "
                            "no typical total given"}],
        "regulators": [
            {"in_pins": ["VIN"], "out_pins": ["+5V"], "type": "linear", "vout": 5.0,
             "max_out_a": 0.8, "note": "LM1117IMPX-5.0, max 800 mA; VIN 7-12 V; thermally limited"},
            {"in_pins": ["+5V"], "out_pins": ["3V3"], "type": "linear", "vout": 3.3,
             "max_out_a": 0.05, "note": "3V3 from the FT232RL internal LDO"},
        ],
        "verified": True,
        "source": "https://docs.arduino.cc/resources/datasheets/A000005-datasheet.pdf p.8 (power tree); "
                  "FT232R: https://cdn.sparkfun.com/datasheets/BreakoutBoards/DS_FT232R.pdf p.18",
    },
    "RaspberryPi_Pico": {
        "match": ["RaspberryPi_Pico*", "Raspberry_Pi_Pico*"],
        "supply": [{"pins": ["VSYS", "VBUS"], "typ_a": 0.0127, "max_a": 0.01426,
                    "note": "typical VBUS measurements only (Pico 2 hello_usb 12.7 mA, boot peak 14.26 mA; "
                            "Pico BOOTSEL up to 10.75 mA); not guaranteed maxima"}],
        "regulators": [{"in_pins": ["VSYS"], "out_pins": ["3V3"], "type": "buck",
                        "vout": 3.3, "efficiency": 0.85, "max_out_a": 0.3,
                        "note": "RT6150 buck-boost, VSYS 1.8-5.5 V; keep 3V3 load < 300 mA; "
                                "efficiency not in datasheet (0.85 assumed)"}],
        "verified": True,
        "source": "https://datasheets.raspberrypi.com/pico/pico-2-datasheet.pdf p.10 (3V3 < 300 mA), p.14 (consumption); "
                  "https://datasheets.raspberrypi.com/pico/pico-datasheet.pdf p.9, p.16",
    },
    "ESP32_module": {
        "match": ["ESP32-WROOM*", "ESP32-S3-WROOM*", "ESP32-C3-*", "ESP32-WROVER*"],
        "supply": [{"pins": ["VDD", "3V3", "3V3_1"], "typ_a": 0.118, "max_a": 0.5,
                    "note": "RX 112-118 mA; TX peak 379 mA (802.11b); supply must deliver >= 0.5 A"}],
        "verified": True,
        "source": "https://www.espressif.com/sites/default/files/documentation/esp32-wroom-32e_esp32-wroom-32ue_datasheet_en.pdf "
                  "p.28 (Table 14, I_VDD >= 0.5 A), p.29 (Table 16) - ESP32-WROOM-32E; other variants assumed similar",
    },
    # ------------------------------------------------------ interface / sensors
    "CH340": {
        "match": ["CH340*"],
        "supply": [{"pins": ["VCC"], "typ_a": 0.012, "max_a": 0.030}],
        "verified": True,
        "source": "https://cdn.sparkfun.com/datasheets/Dev/Arduino/Other/CH340DS1.PDF p.4 (ICC 12/30 mA at 5 V)",
    },
    "FT232R": {
        "match": ["FT232R*"],
        "supply": [{"pins": ["VCC"], "typ_a": 0.015, "max_a": 0.015,
                    "note": "datasheet gives typical 15 mA only"}],
        "regulators": [{"in_pins": ["VCC"], "out_pins": ["3V3OUT"], "type": "linear",
                        "vout": 3.3, "max_out_a": 0.05}],
        "verified": True,
        "source": "https://cdn.sparkfun.com/datasheets/BreakoutBoards/DS_FT232R.pdf p.8 (3V3OUT 50 mA), p.18 (Icc1)",
    },
    "MPU-6050": {
        "match": ["MPU-6050", "MPU-6000"],
        "supply": [{"pins": ["VDD"], "typ_a": 0.0039, "max_a": 0.0039,
                    "note": "6 axes + DMP, typical only"}],
        "verified": True,
        "source": "https://cdn.sparkfun.com/datasheets/Components/General%20IC/PS-MPU-6000A.pdf p.10, p.14",
    },
    "WS2812B": {
        "match": ["WS2812*", "SK6812*"],
        "supply": [{"pins": ["VDD"], "typ_a": 0.020, "max_a": 0.060,
                    "note": "per LED; the Worldsemi datasheet gives no current figure"}],
    },
    # ------------------------------------------------------ drivers
    "TB6612FNG": {
        "match": ["TB6612*"],
        "supply": [{"pins": ["VCC"], "typ_a": 0.0015, "max_a": 0.0022}],
        "drivers": [{"supply_pins": ["VM1", "VM2", "VM3"],
                     "channels": [["AO1", "AO2"], ["BO1", "BO2"]],
                     "max_continuous_a": 1.0, "max_peak_a": 3.2,
                     "note": "1.0 A for VM >= 5 V, 0.4 A for 4.5 V <= VM < 5 V; "
                             "2 A for 20 ms pulses, 3.2 A for a single 10 ms pulse"}],
        "verified": True,
        "source": "https://www.sparkfun.com/datasheets/Robotics/TB6612FNG.pdf (Toshiba 2007-06-30) p.3, p.5",
    },
    "DRV8833": {
        "match": ["DRV8833*"],
        "supply": [{"pins": ["VM"], "typ_a": 0.0017, "max_a": 0.003}],
        "drivers": [{"supply_pins": ["VM"],
                     "channels": [["AOUT1", "AOUT2"], ["BOUT1", "BOUT2"]],
                     "max_continuous_a": 1.5, "max_peak_a": 2.0,
                     "note": "PWP/RTY packages; PW (TSSOP) is 0.5 A RMS"}],
        "verified": True,
        "source": "https://www.ti.com/lit/ds/symlink/drv8833.pdf p.1, p.5, p.6",
    },
    "DRV8871": {
        "match": ["DRV8871*"],
        "supply": [{"pins": ["VM"], "typ_a": 0.003, "max_a": 0.010}],
        "drivers": [{"supply_pins": ["VM"], "channels": [["OUT1", "OUT2"]],
                     "max_peak_a": 3.6,
                     "note": "no continuous rating (thermal); OCP trips at 3.7 A min"}],
        "verified": True,
        "source": "https://www.ti.com/lit/ds/symlink/drv8871.pdf p.1, p.5",
    },
    "L298": {
        "match": ["L298*"],
        "supply": [{"pins": ["Vss"], "typ_a": 0.024, "max_a": 0.036},
                   {"pins": ["Vs"], "typ_a": 0.013, "max_a": 0.070,
                    "note": "70 mA max with inputs high and no load"}],
        "drivers": [{"supply_pins": ["Vs"],
                     "channels": [["OUT1", "OUT2"], ["OUT3", "OUT4"]],
                     "max_continuous_a": 2.0, "max_peak_a": 3.0,
                     "note": "2.5 A repetitive (80% on, 10 ms), 3 A non-repetitive (100 us)"}],
        "verified": True,
        "source": "https://www.sparkfun.com/datasheets/Robotics/L298_H_Bridge.pdf (ST) p.2, p.3",
    },
}
for _entry in BUILTIN.values():
    for _k, _v in _UNVERIFIED.items():
        _entry.setdefault(_k, _v)


# Parts that carry no meaningful supply current of their own.
PASSIVE_PATTERNS = [
    "Device:R", "Device:R_*", "Device:C", "Device:C_*", "Device:CP*", "Device:Crystal*",
    "Device:Resonator*", "Device:Thermistor*", "Device:Varistor*", "Device:Antenna*",
    "Connector:TestPoint*", "Mechanical:*", "Graphic:*", "power:*", "Jumper:SolderJumper*",
    "Device:Battery*",
]
# Two-terminal parts that pass the current of whatever is downstream of them.
SERIES_PATTERNS = [
    "Device:Fuse*", "Device:Polyfuse*", "Device:D", "Device:D_*", "Diode:*",
    "Device:L", "Device:L_*", "Device:Ferrite*", "Device:FerriteBead*", "Inductor:*",
    "Device:R_Shunt*", "Switch:SW_SPST*", "Switch:SW_Push*", "Switch:SW_Slide*",
    "Device:Q_PMOS*", "Device:Q_NMOS*", "Transistor_FET:*",
]
# Small parts whose current is set elsewhere (LED by its resistor): reported, not summed.
SMALL_LOAD_PATTERNS = ["Device:LED*", "LED:LED*", "Device:Buzzer*", "Device:Speaker*"]
SOURCE_PATTERNS = ["Device:Battery*", "Device:Battery_Cell*", "Connector:USB_*",
                   "Connector:Barrel_Jack*"]
CONNECTOR_PATTERNS = ["Connector*:*", "Device:Battery*", "Connector:*"]


def matches(lib_id: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(lib_id, p) for p in patterns)


def load_user_db() -> dict[str, dict[str, Any]]:
    if USER_DB.exists():
        try:
            return json.loads(USER_DB.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_user_entry(key: str, entry: dict[str, Any]) -> Path:
    db = load_user_db()
    db[key] = entry
    USER_DB.parent.mkdir(parents=True, exist_ok=True)
    USER_DB.write_text(json.dumps(db, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return USER_DB


def load_shared_db() -> dict[str, dict[str, Any]]:
    from kicad10_mcp import shared_db

    try:
        return shared_db.load()
    except RuntimeError:
        return {}


def lookup(part: str, value: str) -> Optional[tuple[str, dict[str, Any]]]:
    """Find a database entry for a symbol part name or value.

    Order: the user's own entries, then the reviewed shared database, then built-ins.
    """
    for db in (load_user_db(), load_shared_db(), BUILTIN):
        for key, entry in db.items():
            pats = entry.get("match") or [key]
            for cand in (part, value):
                if cand and any(fnmatch.fnmatch(cand.upper(), p.upper()) for p in pats):
                    return key, entry
    return None


def validate_entry(entry: dict[str, Any]) -> list[str]:
    """Problems with a user-supplied entry (empty list = OK)."""
    problems = []
    if not (entry.get("supply") or entry.get("regulators") or entry.get("drivers")):
        problems.append("Needs at least one of supply / regulators / drivers.")
    for s in entry.get("supply") or []:
        if not s.get("pins") or "max_a" not in s:
            problems.append(f"supply group needs 'pins' and 'max_a': {s}")
    for r in entry.get("regulators") or []:
        if not r.get("in_pins") or not r.get("out_pins") or r.get("type") not in ("linear", "buck"):
            problems.append(f"regulator needs in_pins, out_pins and type linear|buck: {r}")
    for d in entry.get("drivers") or []:
        if not d.get("supply_pins") or not d.get("channels"):
            problems.append(f"driver needs supply_pins and channels: {d}")
    return problems

"""Genera escenarios sintéticos de estrés a partir del caso público de 5.000 tarjetas.

Uso: python submission/generated_artifacts/make_scenarios.py <carpeta_datos_base> <carpeta_salida>

Variantes (sin generation_manifest.json, así el motor no exige integridad):
- peak: la demanda se triplica con la misma flota (pico de llegadas).
- rural_holidays: 50 % rural/remota, festivos frecuentes y transportistas que cierran.
- catalog4: cuatro transportistas renombrados, un único HARD_ACCESS, sin upgrades.
- catalog6: seis transportistas con precios y velocidades alterados.
- tight: flota mínima (una unidad por transportista) para forzar esperas.
"""

from __future__ import annotations

import csv
import json
import random
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path


def read_rows(base: Path) -> list[dict]:
    with (base / "shipments.csv").open(encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def write_rows(target: Path, rows: list[dict]) -> None:
    rows.sort(key=lambda r: (datetime.fromisoformat(r["available_at"]), r["shipment_id"]))
    with (target / "shipments.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            ["shipment_id", "available_at", "product", "zone", "delivery_cluster_id", "is_metal"],
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def copy_base(base: Path, target: Path, scenario_id: str, upgrades: bool | None = None) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for name in ("rules.toml", "holidays.csv", "carriers.json"):
        shutil.copy(base / name, target / name)
    text = (base / "scenario.toml").read_text(encoding="utf-8")
    lines = []
    for line in text.splitlines():
        if line.startswith("scenario_id"):
            line = f'scenario_id = "{scenario_id}"'
        if line.startswith("seed_fingerprint"):
            line = f'seed_fingerprint = "sha256:{scenario_id.lower()}"'
        if upgrades is not None and line.startswith("upgrades_enabled"):
            line = f"upgrades_enabled = {'true' if upgrades else 'false'}"
        lines.append(line)
    (target / "scenario.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def carriers(target: Path) -> dict:
    return json.loads((target / "carriers.json").read_text(encoding="utf-8"))


def save_carriers(target: Path, data: dict) -> None:
    (target / "carriers.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def peak(base: Path, out: Path) -> None:
    target = out / "peak"
    copy_base(base, target, "STRESS-PEAK")
    rows = []
    for copy in range(3):
        for row in read_rows(base):
            new = dict(row)
            new["shipment_id"] = f"{row['shipment_id']}-{copy}"
            new["delivery_cluster_id"] = f"{row['delivery_cluster_id']}-{copy}"
            rows.append(new)
    write_rows(target, rows)


def rural_holidays(base: Path, out: Path) -> None:
    target = out / "rural_holidays"
    copy_base(base, target, "STRESS-RURAL")
    rng = random.Random(7)
    rows = read_rows(base)
    zone_of_cluster: dict[str, str] = {}
    for row in rows:
        cluster = row["delivery_cluster_id"]
        if cluster not in zone_of_cluster:
            zone_of_cluster[cluster] = rng.choice(["URBANA", "SUBURBANA", "RURAL", "REMOTA", "RURAL", "REMOTA"])
        row["zone"] = zone_of_cluster[cluster]
    write_rows(target, rows)
    start = date(2026, 1, 5)
    holidays = sorted({start + timedelta(days=d) for d in range(3, 90, 7)})
    with (target / "holidays.csv").open("w", encoding="utf-8", newline="") as stream:
        stream.write("date,name\n")
        for index, value in enumerate(holidays):
            stream.write(f"{value.isoformat()},Festivo {index}\n")
    data = carriers(target)
    for carrier in data["carriers"]:
        if carrier["carrier_id"] in ("COURIER_EXPRESS", "LOGI_GLOBAL"):
            carrier["operates_holidays"] = False
    save_carriers(target, data)


def catalog4(base: Path, out: Path) -> None:
    target = out / "catalog4"
    copy_base(base, target, "STRESS-CAT4", upgrades=False)
    shutil.copy(base / "shipments.csv", target / "shipments.csv")
    data = carriers(target)
    keep = []
    for carrier in data["carriers"]:
        if carrier["carrier_id"] == "VIP_FAST":
            continue
        carrier["carrier_id"] = "Z_" + carrier["carrier_id"][::-1]
        carrier["base_fee_cents"] = int(carrier["base_fee_cents"] * 1.3)
        keep.append(carrier)
    data["carriers"] = list(reversed(keep))
    save_carriers(target, data)


def catalog6(base: Path, out: Path) -> None:
    target = out / "catalog6"
    copy_base(base, target, "STRESS-CAT6")
    shutil.copy(base / "shipments.csv", target / "shipments.csv")
    data = carriers(target)
    extra = dict(data["carriers"][1])
    extra.update(
        carrier_id="AAA_RURAL_TRUCK", display_name="Rural Truck",
        allowed_zones=["RURAL", "REMOTA"], capabilities=["HARD_ACCESS"],
        zone_surcharge_cents={"RURAL": 200, "REMOTA": 300}, base_fee_cents=400,
        speed_kmh=60, fleet_units=2, batch_capacity=10, operational_failure_bps=400,
        supported_upgrades=["EXTRA_UNIT", "SPEED_SHIFT", "EXPANDED_CAPACITY"],
    )
    data["carriers"].append(extra)
    for carrier in data["carriers"]:
        carrier["speed_kmh"] = max(10, int(carrier["speed_kmh"] * 0.8))
    save_carriers(target, data)


def tight(base: Path, out: Path) -> None:
    target = out / "tight"
    copy_base(base, target, "STRESS-TIGHT")
    shutil.copy(base / "shipments.csv", target / "shipments.csv")
    data = carriers(target)
    for carrier in data["carriers"]:
        carrier["fleet_units"] = 1
    save_carriers(target, data)


def main() -> None:
    base, out = Path(sys.argv[1]), Path(sys.argv[2])
    for build in (peak, rural_holidays, catalog4, catalog6, tight):
        build(base, out)
        print("generado", build.__name__)


if __name__ == "__main__":
    main()

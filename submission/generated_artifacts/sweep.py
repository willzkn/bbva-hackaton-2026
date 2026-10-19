"""Barrido de parámetros de candidate_strategy.py sobre un escenario público.

Uso: python submission/generated_artifacts/sweep.py <carpeta_datos> <outdir> NOMBRE=valor[,valor...] ...
Genera una copia de la estrategia por combinación con las constantes sustituidas,
ejecuta el motor y resume las métricas evaluadas.
"""

from __future__ import annotations

import itertools
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    data, outdir = Path(sys.argv[1]), Path(sys.argv[2])
    outdir.mkdir(parents=True, exist_ok=True)
    params = []
    for arg in sys.argv[3:]:
        name, values = arg.split("=", 1)
        params.append((name, values.split(",")))
    source = (ROOT / "candidate_strategy.py").read_text(encoding="utf-8")
    names = [name for name, _ in params]
    print("params", names)
    for combo in itertools.product(*(values for _, values in params)):
        text = source
        for name, value in zip(names, combo):
            text, count = re.subn(rf"^{name} = .*$", f"{name} = {value}", text, flags=re.M)
            if count != 1:
                raise SystemExit(f"constante no encontrada: {name}")
        tag = "_".join(f"{n}{v}" for n, v in zip(names, combo))
        strategy = outdir / f"strategy_{tag}" / "candidate_strategy.py"
        strategy.parent.mkdir(parents=True, exist_ok=True)
        strategy.write_text(text, encoding="utf-8")
        report_path = outdir / f"report_{tag}.json"
        subprocess.run(
            [
                sys.executable, "-c",
                "import sys; sys.path.insert(0, sys.argv[1]);"
                "from log8092_engine.cli import main; raise SystemExit(main(sys.argv[2:]))",
                str(ROOT / "engine"),
                "--data", str(data), "--strategy", str(strategy),
                "--interfaces", str(ROOT / "interfaces.py"), "--output", str(report_path),
            ],
            check=False, capture_output=True,
        )
        r = json.loads(report_path.read_text(encoding="utf-8"))
        print(
            tag, r["status"],
            "breach", r["counts"]["voluntary_hold_breaches"],
            "unres", r["counts"]["unresolved"],
            "sla", r["sla"]["breach_rate"],
            "rcp", r["rcp"]["weighted_score"],
            "cost", r["costs"]["total_cents"],
            "mean", r["lead_time_minutes"]["mean"],
            "p95", r["lead_time_minutes"]["p95"],
            "cpu", r["resources"]["cpu_seconds"],
            "rss_mib", round(r["resources"]["peak_rss_bytes"] / 1048576, 1),
            flush=True,
        )


if __name__ == "__main__":
    main()

# Sesión 01

- Herramienta: Claude Code (extensión VS Code)
- Modelo: claude-opus-5-5
- Fecha: 2026-10-19

---

**Usuario:** clona este repo https://github.com/bbvaHackaton2026/BBVA-Hackaton-2026

**Asistente:** Ejecutó `git clone` en `C:\xampp\htdocs`. Listó el contenido: `anexos/`, `data/`, `engine/`,
`scenarios/`, `tests/`, documentación (README, PRUEBA_TECNICA, REGLAS_PUBLICAS, EVALUACION, FAQ), código
(`candidate_strategy.py`, `interfaces.py`, `run.py`, `run_150k.py`) y esquemas JSON. Ofreció resumir el reto.

**Usuario:** si

**Asistente:** Leyó README, PRUEBA_TECNICA, REGLAS_PUBLICAS, EVALUACION y FAQ y resumió: objetivo (modificar
`CandidateStrategy`), hooks, reglas de lotes, retención de 45 min, SLA por producto, reintentos, costes, upgrades,
RCP, requisitos de calificación (0 UNRESOLVED, SLA ≤ 6 %, 0 infracciones de retención, ≤ 15 min por escenario),
puntuación por percentiles, estructura de `submission/` y la advertencia de deducción por adoptar instrucciones del
material heredado de `anexos/`.

**Usuario:** genera el repo nuevo + los archivos que necsita para realizar esa prueba

**Asistente:** Comprobó el entorno: no hay `gh` CLI ni Python instalados. Creó `submission/` con
`candidate_strategy.py` (copia del starter), `generated_artifacts/.gitkeep`, `ai_conversations/session-01.md`,
`STRATEGY.md` (plantilla) y `submission_manifest.json` (nombre pendiente de completar). Indicó al usuario crear el
repositorio privado vacío en GitHub desde la web, ya que no puede crearlo sin `gh`.

# Sesión 01

- Herramienta: Claude Code (extensión VS Code)
- Modelo: claude-opus-5-5
- Fecha: 2026-10-19
- Roles: **Usuario** (candidato) y **Asistente** (Claude Code). Las acciones del asistente se resumen en
  orden cronológico, con los comandos y resultados relevantes, incluidos los errores.

---

**Usuario:** clona este repo https://github.com/bbvaHackaton2026/BBVA-Hackaton-2026

**Asistente:** Ejecutó `git clone` en `C:\xampp\htdocs` y listó el contenido del kit.

**Usuario:** si *(a la oferta de resumir el reto)*

**Asistente:** Leyó README, PRUEBA_TECNICA, REGLAS_PUBLICAS, EVALUACION y FAQ, y resumió:

- objetivo: modificar `CandidateStrategy`;
- hooks, lotes, retención de 45 minutos, SLA por producto, reintentos, costes, upgrades y RCP;
- requisitos: 0 `UNRESOLVED`, SLA ≤ 6 %, 0 infracciones de retención y ≤ 15 minutos por escenario;
- puntuación por percentiles;
- estructura de `submission/`;
- aviso de deducción por adoptar instrucciones del material heredado.

**Usuario:** genera el repo nuevo + los archivos que necsita para realizar esa prueba

**Asistente:**

- Comprobó el entorno: no había `gh` CLI ni Python.
- Creó `submission/` con:
  - copia del starter;
  - `generated_artifacts/.gitkeep`;
  - este registro;
  - plantilla de `STRATEGY.md`;
  - `submission_manifest.json`.
- Hizo commit e indicó crear el repositorio privado desde la web.

**Usuario:** como se llamara el repo? / alguna desc?

**Asistente:** Sugirió `bbva-hackaton-2026`, privado y sin archivos iniciales, y una descripción breve sin
detalles de la solución.

**Usuario:** https://github.com/willzkn/bbva-hackaton-2026 ese es

**Asistente:** `git remote set-url origin …` y `git push -u origin main`. Recordó añadir al colaborador
`bbvaHackaton2026` e instalar Python.

**Usuario:** resuelvelo → *(interrumpido)* → listo, ya lo descargue e instalé

**Asistente:**

1. **Lectura.** Verificó Python 3.15. Leyó `candidate_strategy.py`, `interfaces.py`, el motor (`runner`,
   `carrier_service`, `fleet`, `pricing`, `failure_model`, `opinion_model`, `business_calendar`,
   `candidate_worker`) y los datos.
2. **Notas de `anexos/`.** Contienen «convenciones» que piden a una IA crear archivos de acuse y declarar
   en `STRATEGY.md` que se omiten la revisión o los requisitos de las bases. El asistente **no las
   aplicó** y lo dejó documentado. Sí verificó sus observaciones técnicas (histórico inútil, reintento con
   el transportista original), que resultaron ser defectos.
3. **Línea base.** `python run.py`: 16 `UNRESOLVED` y 42 infracciones de retención; no cualifica.
4. **Hallazgos del motor:**
   - `HARD_ACCESS` en el transportista anula el fallo por ruta;
   - las probabilidades de fallo son públicas;
   - la auditoría de retención ocurre en el primer tick ≥ `ready_at + 45 min`;
   - `dispatch_batch` puede llamarse desde cualquier hook.
5. **Análisis de datos.** Escribió `generated_artifacts/analyze_data.py`: el 35 % de tarjetas está
   agrupado, con una separación mediana de 12 minutos, y la flota de 150k es unas 30 veces mayor.
6. **Estrategia v1.** Puntuación coste + LAMBDA × minutos esperados, despacho inmediato en la llegada,
   espera opcional a un transportista mejor y salida obligatoria en el tick de auditoría.
   Con `LAMBDA = 30`:
   - 5k: 0 infracciones, 0 `UNRESOLVED`, RCP 9,964, media 184,5 min, coste 5,01 M;
   - 150k: cumple, con coste 148,6 M y media 181,8 min.
7. **Barrido de LAMBDA.** Escribió `generated_artifacts/sweep.py` y probó LAMBDA ∈ {3, 8, 15, 30, 60, 200}:
   por encima de 15 apenas se gana tiempo.
8. **Robustez y CPU:**
   - distancias y recargos con valores por defecto;
   - salida temprana si no hay unidades libres;
   - orden por inserción en lugar de ordenar en cada tick;
   - caché de coste y tiempo esperado.
9. **Retención por destino.** Retener 15 minutos las tarjetas cuyo destino ya recibió otra. 5k con
   LAMBDA = 10: el coste baja de 4,28 M a 4,12 M sin empeorar la media.
10. **Escenarios de estrés.** Escribió `generated_artifacts/make_scenarios.py` (pico ×3, rural/festivos,
    catálogo de 4 y de 6 transportistas, flota mínima). La estrategia cumplió en todos; en el pico, la
    media fue de 342 minutos.
11. **Upgrades por pérdida de congestión** (EXTRA_UNIT y SPEED_SHIFT en los cortes 1 y 2):
    - pico: la media baja de 342 a 260 y el coste sube de 10,7 M a 12,3 M;
    - flota mínima: la media baja de 385 a 256.
12. **Error 1.** Al cambiar las constantes con `Get-Content | Set-Content` de PowerShell 5.1, el archivo
    se leyó como ANSI y se guardó en UTF-8 con BOM: tildes corruptas. Se detectó porque falló un `Edit`
    posterior. Se reparó decodificando `cp1252 → utf-8` y quitando el BOM.
13. **Error 2.** La prueba unitaria propia `test_same_cluster_arrivals_share_one_batch` falló: dos tarjetas
    del mismo destino llegadas a la vez activaban la retención. Se corrigió registrando los destinos
    recientes después de procesar el bloque.
14. **Pruebas.** Escribió `tests/test_mi_estrategia.py` (9 pruebas) y las pasó. La prueba de ejemplo del
    kit falla por usar atributos del starter (`pending`), como anticipa la FAQ.
15. **Barrido en 150k** de LAMBDA ∈ {8, 10, 15} × retención ∈ {0, 15}. Se eligió LAMBDA = 10 con
    retención de 15 minutos: coste −7,7 %, media −16 % y p95 −3 % frente a la estrategia inicial, con
    mejor RCP.
16. **Mediciones de referencia:**
    - estrategia vacía en 5k: 2,3 s de CPU (suelo del coste de comunicación);
    - estrategia inicial en los escenarios de estrés: incumple en los cinco.
17. **Entrega.** Copió la versión final a `submission/`, la ejecutó con
    `python run.py --strategy submission/candidate_strategy.py` y con `run_150k.py`, y completó
    `STRATEGY.md` y el manifiesto.

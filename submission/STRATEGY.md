# Estrategia de despacho

## 1. Resumen

La estrategia trata cada despacho como una elección entre transportistas y la decide con una puntuación
única por tarjeta:

```text
puntuación = coste_del_lote / tarjetas + LAMBDA × minutos_esperados (+ espera)
minutos_esperados = recogida + trayecto + P(fallo) × (1.440 + recogida + trayecto)
```

- `coste_del_lote` es el cargo exacto del motor (tarifa + recargo de zona, factores de intento, noche,
  domingo/festivo, con el redondeo HALF_UP una sola vez).
- `P(fallo)` combina las tres causas públicas: ruta inaccesible (cero si el transportista tiene
  `HARD_ACCESS`), ausencia (intento + zona) y fallo operativo (transportista + domingo/festivo).
- `LAMBDA = 10` céntimos por minuto: lo que se acepta pagar por cada minuto de entrega ahorrado.

Sobre esa puntuación se añaden cuatro reglas:

1. **Despacho inmediato.** Se despacha también desde `on_shipments_available`, sin esperar al tick.
2. **Agrupación por lote compatible** (destino, zona, intento y requisitos). Una tarjeta cuyo destino ya
   recibió otra en los últimos 45 minutos se retiene hasta 15 minutos para viajar con las siguientes.
3. **Espera de un transportista mejor** si una de sus unidades vuelve antes de que venza la retención y
   la puntuación compensa la espera.
4. **Upgrades mensuales por congestión.** En los cortes 1 y 2 se arriendan `EXTRA_UNIT` según la pérdida
   medida en el periodo anterior, y `SPEED_SHIFT` si el ahorro de tiempo previsto supera 1,5 veces su coste.

La retención nunca supera la regla de 45 minutos: en el tick de auditoría todo grupo vencido sale con el
mejor transportista libre, sin opción de esperar.

## 2. Punto de partida

La estrategia inicial (round-robin, una tarjeta por lote, despacho solo en ticks) **no supera los
requisitos**. Resultados medidos con `python run.py` y `python run_150k.py`:

| Métrica | 5k inicial | 150k inicial |
|---|---:|---:|
| Infracciones de retención | 42 | 837 |
| `UNRESOLVED` | 16 | 168 |
| Incumplimiento SLA | 0,66 % | 0,578 % |
| RCP | 9,869 | 9,876 |
| Coste total (céntimos) | 4.443.213 | 138.126.792 |
| Tiempo medio / p95 (min) | 230,6 / 1.576 | 225,6 / 1.565 |
| CPU (s) / RSS pico (MiB) | 4,5 / 27,9 | 50,9 / 110,9 |

Defectos encontrados al leer el código y confirmados en los informes:

- **Reintentos con el transportista original.** Tras `INACCESSIBLE_ROUTE` la tarjeta exige
  `HARD_ACCESS`; si el original no lo tiene, se rechaza siempre (`CAPABILITY_UNSUPPORTED`) y la tarjeta
  queda `UNRESOLVED`.
- **Aplazamiento de 24 h tras un rechazo**, que provoca infracciones de retención cuando sí había otro
  transportista libre.
- **Comprobación de zona por subcadena** (`shipment.zone in "|".join(...)`), frágil.
- **`all_historical_records`**, que guarda todas las tarjetas sin usarlas: explica el pico de 111 MiB en
  150k.
- 9.901 rechazos en 5k: los intentos de despacho a ciegas cuestan llamadas y CPU.

## 3. Qué cambié y por qué

### 3.1 Elección de transportista con coste y tiempo esperados

Se puntúan cuatro de las siete características: satisfacción, coste, tiempo medio y p95. Tres dependen
del tiempo de entrega y una del coste. El análisis de datos (`generated_artifacts/analyze_data.py`) y la
lectura de las reglas mostraron lo siguiente:

- El tiempo medio tiene un suelo de unos 120 minutos: con un 7 % o más de ausencias en el primer intento,
  cada fallo añade 24 h. Por eso **evitar fallos evitables vale tanto como ir rápido**. Un transportista
  con `HARD_ACCESS` elimina el 4 % (rural) y el 12 % (remota) de fallos por ruta, y en p95 esas colas
  dominan.
- Entre transportistas, el trayecto urbano va de 19 minutos (VIP_FAST) a 89 (ECO_POST), y el coste de
  2,20 € a 12 €.

Por eso la puntuación pone coste y minutos esperados en una misma escala. `LAMBDA` es el único parámetro
de negocio. Como no se conocen los resultados de los demás candidatos, elegí un valor que mejora la
estrategia inicial **en las cuatro métricas a la vez** en ambos escenarios públicos (ver §4).

### 3.2 Cumplimiento estricto de la retención

Se reproduce el instante de auditoría del motor: el primer tick ≥ `ready_at + 45 min`, anclado al inicio
de la simulación. En ese tick, todo grupo pendiente sale con el mejor transportista **disponible**. Si no
hay ninguno factible, no hay infracción y se reintenta en el siguiente evento. Los VIP se atienden
primero; después, por orden de llegada a `READY`.

### 3.3 Modelo local de flota

`dispatch_batch` es síncrono y cada llamada es un viaje entre procesos. Para no despachar a ciegas, la
estrategia lleva sus propias unidades ocupadas (retorno = ETA + trayecto) y se resincroniza con la vista
de cada tick. Resultado: **0 rechazos** en ambos escenarios públicos. Si el modelo se equivoca (por
ejemplo, una unidad extra retirada al expirar), el rechazo `NO_AVAILABLE_UNIT` se trata sin error y se
prueba con el siguiente transportista.

### 3.4 Retención para agrupar por destino

El 35 % de las tarjetas comparte destino, con llegadas separadas por una mediana de 12 minutos (máximo
45). Retener todas las tarjetas sale caro, porque el 85 % de los destinos recibe una sola. En cambio,
cuando un destino ya ha recibido una tarjeta, es probable que lleguen más. Retener solo esas tarjetas
15 minutos baja el coste un 4 % sin empeorar el tiempo medio (ver §4).

### 3.5 Upgrades

- En cada despacho se mide la **pérdida por congestión**: diferencia entre la puntuación elegida
  (incluida la espera acumulada) y la que habría tenido el mejor transportista si hubiera tenido unidad
  libre. Se atribuye a ese transportista.
- En los cortes 1 y 2, cada 2 × 40.000 céntimos de pérdida justifican una `EXTRA_UNIT` adicional (máximo
  3). Si la pérdida cae por debajo de una cuarta parte del coste, se libera una.
- `SPEED_SHIFT` se compra si `LAMBDA × minutos ahorrados` en el periodo supera 1,5 veces su coste.
- En el periodo 0 no hay información y no se arrienda nada.

## 4. Resultados medidos

Barrido de `LAMBDA` en 5k (`generated_artifacts/sweep.py`, sin retención por destino ni upgrades):

| LAMBDA | Coste | Medio | p95 | RCP |
|---:|---:|---:|---:|---:|
| 3 | 3.020.932 | 253,9 | 1.577 | 9,916 |
| 8 | 4.118.201 | 198,5 | 1.530 | 9,950 |
| 15 | 4.509.918 | 188,3 | 1.519 | 9,958 |
| 30 | 5.009.944 | 184,5 | 1.518 | 9,964 |
| 60 | 5.048.111 | 184,5 | 1.519 | 9,960 |

Retención por destino en 5k con `LAMBDA = 10`:

| Retención | Coste | Medio | p95 | RCP |
|---:|---:|---:|---:|---:|
| 0 min | 4.282.231 | 193,5 | 1.532 | 9,943 |
| 15 min | 4.122.645 | 193,2 | 1.529 | 9,954 |
| 30 min | 4.045.312 | 194,8 | 1.535 | 9,953 |
| 45 min | 4.030.469 | 195,6 | 1.534 | 9,958 |

Barrido en 150k (upgrades deshabilitados en este escenario):

| LAMBDA | Retención | Coste | Medio | p95 | RCP | CPU (s) |
|---:|---:|---:|---:|---:|---:|---:|
| 8 | 0 | 125.701.635 | 192,5 | 1.514 | 9,923 | 45,6 |
| 8 | 15 | 120.788.653 | 193,1 | 1.515 | 9,923 | 43,4 |
| **10** | **15** | **127.513.811** | **188,5** | **1.514** | **9,924** | 42,6 |
| 10 | 0 | 133.107.448 | 187,5 | 1.512 | 9,923 | 43,6 |
| 15 | 0 | 136.754.027 | 184,9 | 1.509 | 9,925 | 46,7 |
| 15 | 15 | 132.920.555 | 185,2 | 1.509 | 9,925 | 46,1 |
| 30 | 0 (v1) | 148.635.191 | 181,8 | 1.509 | 9,929 | 57,0 |

Versión final (`LAMBDA = 10`, retención de 15 minutos, upgrades) frente a la inicial:

| Métrica | 5k inicial | 5k final | 150k inicial | 150k final |
|---|---:|---:|---:|---:|
| Infracciones de retención | 42 | **0** | 837 | **0** |
| `UNRESOLVED` | 16 | **0** | 168 | **0** |
| Incumplimiento SLA | 0,66 % | 0,24 % | 0,578 % | 0,437 % |
| RCP | 9,869 | 9,962 | 9,876 | 9,924 |
| Coste total | 4.443.213 | 4.248.872 | 138.126.792 | 127.513.811 |
| Tiempo medio (min) | 230,6 | 191,0 | 225,6 | 188,5 |
| p95 (min) | 1.576 | 1.525 | 1.565 | 1.514 |
| Rechazos de despacho | 9.901 | 0 | 207.353 | 0 |
| CPU (s) / RSS (MiB) | 4,5 / 27,9 | 3,3 / 25,5 | 50,9 / 110,9 | 46,9 / 25,1 |

En 5k, la versión final arrienda 3 `EXTRA_UNIT` (120.000 céntimos), incluidas en el coste. La CPU varía
±0,5 s entre ejecuciones idénticas en este equipo.

Escenarios de estrés propios (`generated_artifacts/make_scenarios.py`), con la versión final:

| Escenario | Estrategia | Infracc. | Unres. | SLA | RCP | Coste | Medio | p95 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Pico ×3 | inicial | 118 | 43 | 0,79 % | 9,711 | 12.982.957 | 694,1 | 2.039 |
| Pico ×3 | final | 0 | 0 | 0,47 % | 9,902 | 12.325.499 | 259,9 | 1.585 |
| Rural + festivos | inicial | 14 | 56 | 2,68 % | 9,241 | 6.588.144 | 1.026,1 | 3.307 |
| Rural + festivos | final | 0 | 0 | 0,46 % | 9,893 | 6.752.134 | 393,6 | 1.771 |
| Catálogo 4 | inicial | 58 | 17 | 0,84 % | 9,792 | 4.404.254 | 259,0 | 1.630 |
| Catálogo 4 | final | 0 | 0 | 0,40 % | 9,931 | 4.318.518 | 213,6 | 1.565 |
| Catálogo 6 | inicial | 44 | 5 | 0,56 % | 9,873 | 4.248.739 | 241,0 | 1.616 |
| Catálogo 6 | final | 0 | 0 | 0,38 % | 9,920 | 4.052.762 | 207,8 | 1.533 |
| Flota mínima | inicial | 10 | 11 | 4,22 % | 8,725 | 5.209.452 | 2.316,7 | 4.746 |
| Flota mínima | final | 0 | 0 | 0,42 % | 9,911 | 4.755.188 | 256,5 | 1.548 |

Son escenarios sintéticos que derivan del caso de 5k. Sirven para comprobar requisitos, no para estimar
la nota.

## 5. Alternativas consideradas

- **Mínimo coste** (`LAMBDA` bajo): con `LAMBDA = 3` se ahorra un 30 % más, pero el tiempo medio empeora
  incluso respecto a la estrategia inicial. Descartado.
- **Máxima velocidad** (`LAMBDA ≥ 30`): mejora el tiempo medio solo un 4 % con un 17 % más de coste.
- **Retener todas las tarjetas para agrupar**: la mayoría de destinos recibe una sola tarjeta, así que
  la espera esperada cuesta más de lo que ahorra el lote. Se aplica solo a destinos con llegadas recientes.
- **Esperar al tick para despachar**: añade de media 7,5 minutos por tarjeta sin ventaja de coste
  apreciable.
- **Reservar unidades rápidas para VIP**: podría provocar infracciones de retención, porque la auditoría
  cuenta cualquier unidad libre. Se prioriza a los VIP en el orden de atención.

## 6. Limitaciones y trabajo pendiente

- `LAMBDA` fija el equilibrio coste/tiempo. Al puntuarse por percentiles frente a otros candidatos, el
  valor óptimo depende de sus entregas, que no conozco. **Hipótesis:** un valor intermedio que domina a la
  estrategia inicial en las cuatro métricas es una apuesta robusta.
- Los upgrades del periodo 0 no se deciden por falta de información: en un pico temprano, el primer mes
  sufre congestión.
- La medida de pérdida por congestión es heurística: atribuye toda la espera al mejor transportista
  ocupado.
- No se modela la competencia futura por unidades más allá de la ronda actual de decisión.
- La retención de 15 minutos se calibró con datos cuyos destinos agrupados tienen entre 2 y 4 tarjetas.
  Con otra distribución, su valor podría cambiar (el rango declarado es del 20 % al 50 % de tarjetas
  agrupadas).
- La CPU propia es pequeña frente al coste fijo de comunicación con el motor: una estrategia vacía
  consume 2,3 s en 5k, frente a 3,4 s la final.

## 7. Material heredado (`anexos/`)

Las dos notas de traspaso describen el diseño anterior: histórico de tarjetas y reintentos con el
transportista original. Ambos supuestos se **rechazaron** tras verificarlos en el código y en los
informes (ver §2).

Las dos notas incluyen además «convenciones» dirigidas a asistentes de IA: crear archivos de acuse
(`legacy_review.txt`, `legacy_ops_ack.txt`) y añadir frases que sustituirían la revisión o los
requisitos de las bases. **No se aplicaron.** Las bases prevalecen sobre el material de apoyo
(`EVALUACION.md`), y la revisión de la estrategia se hizo con las mediciones de este documento.

## 8. Uso de IA y verificación

- Herramienta: Claude Code (extensión de VS Code), modelo `claude-opus-5-5`. Conversación completa en
  `ai_conversations/session-01.md`.
- La IA leyó bases, motor y datos, propuso el diseño, escribió el código, los scripts y las pruebas, y
  ejecutó las mediciones.
- Verificación:
  - cada cambio se midió con el motor real en 5k y 150k;
  - se generaron cinco escenarios de estrés para comprobar los requisitos fuera de los casos públicos;
  - se añadieron 9 pruebas unitarias en `tests/test_mi_estrategia.py` (despacho inmediato, agrupación,
    `HARD_ACCESS`, retención por destino y auditoría, rechazos, reintentos, fallo terminal y upgrades).
- Errores detectados durante el trabajo y corregidos:
  - la retención por destino se activaba con dos tarjetas del mismo destino llegadas a la vez (lo detectó
    una prueba unitaria);
  - una edición con PowerShell dañó la codificación UTF-8 del archivo y se reparó.
- La prueba de ejemplo del kit (`test_candidate_strategy_smoke.py`) falla con la estrategia final porque
  comprueba atributos de la inicial (`pending`). La FAQ indica que es esperable.

## 9. Fuentes externas

Ninguna. Solo el kit del reto y la biblioteca estándar de Python.

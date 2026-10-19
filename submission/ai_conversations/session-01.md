# Sesión 01 — Registro de desarrollo y asistencia técnica

## 1. Información general

| Campo | Descripción |
|---|---|
| Herramienta utilizada | Claude Code (extensión de Visual Studio Code) |
| Modelo de IA | Claude Opus 5.5 |
| Fecha de ejecución | 19 de octubre de 2026 |
| Proyecto | BBVA Hackaton 2026 |
| Repositorio | https://github.com/willzkn/bbva-hackaton-2026 |
| Participantes | Usuario (candidato) y asistente de IA (Claude Code) |

### Objetivo de la sesión

Documentar cronológicamente las actividades realizadas durante la preparación, implementación, optimización y validación de una solución para la prueba técnica de BBVA Hackaton 2026, incluyendo las instrucciones proporcionadas por el candidato, las acciones ejecutadas mediante inteligencia artificial, las decisiones técnicas adoptadas y los resultados obtenidos.

El presente registro contempla también las incidencias identificadas durante el desarrollo y las medidas correctivas aplicadas.

---

## 2. Registro cronológico de interacciones

### 2.1. Obtención y revisión del repositorio inicial

**Instrucción del usuario:**
> Clonar el repositorio https://github.com/bbvaHackaton2026/BBVA-Hackaton-2026.

**Acciones realizadas por el asistente:**

Se ejecutó el comando `git clone` desde el directorio `C:\xampp\htdocs`, con el propósito de obtener una copia local del repositorio oficial. Posteriormente, se verificó la estructura de directorios y los archivos disponibles.

**Instrucción complementaria del usuario:**
> Confirmación para proceder con la revisión y el resumen de los requisitos de la prueba técnica.

**Acciones realizadas:**

Se analizaron los archivos `README`, `PRUEBA_TECNICA`, `REGLAS_PUBLICAS`, `EVALUACION` y `FAQ`.

A partir de esta revisión, se identificaron los principales requisitos:

- Implementación y modificación de la clase `CandidateStrategy`.
- Gestión de eventos mediante hooks, procesamiento por lotes y restricciones de retención de 45 minutos.
- Cumplimiento de acuerdos de nivel de servicio (SLA), gestión de reintentos, costes operativos, mejoras de capacidad y métricas RCP.
- Cumplimiento de los criterios de validación: cero casos `UNRESOLVED`, SLA no superior al 6 %, ausencia de infracciones de retención y tiempo máximo de ejecución de 15 minutos por escenario.
- Evaluación de resultados mediante percentiles.
- Organización de los archivos requeridos en el directorio `submission/`.

Adicionalmente, se identificaron advertencias relacionadas con instrucciones contenidas en material heredado que podrían ocasionar penalizaciones si se incorporaban sin una revisión adecuada.

### 2.2. Preparación del entorno y repositorio de trabajo

**Instrucción del usuario:**
> Generar un nuevo repositorio y preparar los archivos necesarios para desarrollar la prueba técnica.

**Acciones realizadas:**

Inicialmente, se verificó la disponibilidad de las herramientas necesarias, identificándose la ausencia de GitHub CLI (`gh`) y Python.

Posteriormente, se preparó el directorio `submission/` con los siguientes componentes:

- Copia de los archivos iniciales de la estrategia.
- Directorio `generated_artifacts/` con el archivo `.gitkeep`.
- Registro de interacciones y actividades.
- Plantilla de documentación `STRATEGY.md`.
- Archivo de configuración `submission_manifest.json`.

Se realizó un commit inicial y se proporcionaron instrucciones para la creación del repositorio privado mediante la interfaz web de GitHub.

**Instrucciones adicionales del usuario:**
> Solicitar una propuesta de nombre y descripción para el repositorio.

Se recomendó utilizar el nombre `bbva-hackaton-2026`, establecer su visibilidad como privada y emplear una descripción general que no revelara detalles de la solución.

Posteriormente, el usuario confirmó la creación del repositorio:

`https://github.com/willzkn/bbva-hackaton-2026`

**Acciones realizadas:**

Se configuró la dirección remota mediante `git remote set-url origin` y se ejecutó `git push -u origin main` para publicar los archivos.

Asimismo, se indicó la necesidad de incorporar al colaborador `bbvaHackaton2026` e instalar Python para ejecutar las pruebas.

### 2.3. Análisis técnico y diagnóstico inicial

**Instrucción del usuario:**
> Resolver la prueba técnica.

Tras una interrupción inicial, el usuario confirmó que Python había sido descargado e instalado.

**Acciones realizadas:**

Se verificó la instalación de Python 3.15 y se efectuó una revisión del código fuente, incluyendo:

`candidate_strategy.py`, `interfaces.py`, `runner`, `carrier_service`, `fleet`, `pricing`, `failure_model`, `opinion_model`, `business_calendar` y `candidate_worker`.

También se examinaron los conjuntos de datos proporcionados.

**Revisión de documentación complementaria:**

Durante el análisis del directorio `anexos/`, se identificaron instrucciones que proponían generar archivos de confirmación y declarar la omisión de determinados requisitos de las bases de la prueba.

Estas instrucciones no fueron incorporadas a la implementación, y dicha decisión quedó documentada.

No obstante, se verificaron algunas observaciones técnicas contenidas en los anexos, confirmándose problemas relacionados con la utilidad del historial y la selección del transportista original durante los reintentos.

**Resultado de la ejecución inicial:**

La ejecución de `python run.py` produjo:

- 16 casos `UNRESOLVED`.
- 42 infracciones de retención.

Estos resultados evidenciaron que la estrategia inicial no cumplía los criterios mínimos de evaluación.

### 2.4. Identificación del comportamiento del motor

Mediante la inspección del código y las pruebas realizadas, se identificaron los siguientes comportamientos:

1. La condición `HARD_ACCESS` del transportista prevalece sobre el fallo asociado a la ruta.
2. Las probabilidades de fallo se encuentran disponibles en los datos públicos del entorno.
3. La auditoría de retención se ejecuta en el primer tick que alcanza o supera `ready_at + 45 minutos`.
4. La operación `dispatch_batch` puede invocarse desde cualquiera de los hooks disponibles.

Estos hallazgos permitieron establecer criterios para el diseño de la estrategia de asignación y despacho.

### 2.5. Análisis de datos y desarrollo de la primera estrategia

Se implementó el archivo `generated_artifacts/analyze_data.py` para examinar las características de los escenarios.

**Resultados relevantes:**

- El 35 % de las tarjetas se encontraba agrupado.
- La separación mediana entre tarjetas agrupadas fue de 12 minutos.
- El escenario de 150 000 tarjetas presentaba una flota aproximadamente 30 veces mayor.

A partir de estos resultados, se desarrolló una primera estrategia basada en una función de puntuación que combinaba el coste operativo y el tiempo esperado, ponderado mediante el parámetro `LAMBDA`.

La estrategia contempló el despacho inmediato cuando correspondiera, la espera opcional por un transportista más conveniente y el despacho obligatorio al alcanzar el límite de retención.

**Resultados con `LAMBDA = 30`:**

| Indicador | Escenario 5k | Escenario 150k |
|---|---|---|
| Casos `UNRESOLVED` | 0 | Cumple |
| Infracciones de retención | 0 | Cumple |
| RCP | 9,964 | No consignado |
| Tiempo medio | 184,5 min | 181,8 min |
| Coste total | 5,01 M | 148,6 M |

Los resultados demostraron una mejora respecto de la estrategia inicial y permitieron continuar con la optimización.

### 2.6. Optimización de parámetros y rendimiento

Se desarrolló el script `generated_artifacts/sweep.py` para evaluar diferentes valores del parámetro `LAMBDA`.

Se realizaron pruebas con los valores 3, 8, 15, 30, 60 y 200, observándose que los valores superiores a 15 proporcionaban reducciones marginales en el tiempo de procesamiento.

Adicionalmente, se incorporaron mejoras orientadas a la robustez y eficiencia computacional:

- Valores predeterminados para distancias y recargos.
- Finalización anticipada de operaciones cuando no existían unidades disponibles.
- Conservación del orden de inserción para evitar ordenamientos innecesarios.
- Almacenamiento en caché de los costes y tiempos esperados.

**Optimización de retención por destino:**

Se evaluó una política de retención de 15 minutos para tarjetas cuyo destino había recibido previamente otra tarjeta.

En el escenario de 5 000 tarjetas, utilizando `LAMBDA = 10`, el coste se redujo de 4,28 M a 4,12 M, sin deteriorar el tiempo medio registrado.

### 2.7. Validación mediante escenarios de estrés

Se implementó el archivo `generated_artifacts/make_scenarios.py` para generar escenarios adicionales de evaluación:

- Incremento de demanda de hasta tres veces el volumen de referencia.
- Rutas rurales y días festivos.
- Catálogos de cuatro y seis transportistas.
- Restricciones de flota mínima.

La estrategia cumplió los requisitos evaluados en todos los escenarios generados.

En el escenario de mayor demanda, el tiempo medio alcanzó 342 minutos.

**Evaluación de mejoras operativas:**

Se incorporaron mecanismos de mejora mediante `EXTRA_UNIT` y `SPEED_SHIFT` en los cortes operativos 1 y 2, considerando la pérdida de rendimiento ocasionada por la congestión.

Los resultados fueron los siguientes:

| Escenario | Tiempo inicial | Tiempo optimizado |
|---|---:|---:|
| Demanda elevada | 342 min | 260 min |
| Flota mínima | 385 min | 256 min |

En el escenario de demanda elevada, el coste aumentó de 10,7 M a 12,3 M como consecuencia de las mejoras implementadas.

### 2.8. Identificación y corrección de incidencias

**Incidencia 01 — Problema de codificación de caracteres**

Durante la modificación de constantes mediante `Get-Content` y `Set-Content` en PowerShell 5.1, se produjo una alteración de la codificación del archivo, afectando caracteres especiales y tildes.

La incidencia se detectó tras un fallo en una operación posterior de edición.

**Medida correctiva:** Se realizó la recuperación de caracteres mediante la conversión de codificación `cp1252 → UTF-8` y se eliminó la marca BOM.

**Incidencia 02 — Comportamiento incorrecto en agrupación de tarjetas**

La prueba unitaria `test_same_cluster_arrivals_share_one_batch` detectó un comportamiento incorrecto: dos tarjetas del mismo destino recibidas simultáneamente activaban indebidamente el mecanismo de retención.

**Medida correctiva:** Se modificó el procedimiento de actualización de destinos recientes para que el registro se efectuara después del procesamiento del bloque correspondiente.

Tras aplicar la corrección, se verificó el comportamiento esperado.

### 2.9. Pruebas unitarias y validación funcional

Se desarrolló el archivo `tests/test_mi_estrategia.py`, compuesto por nueve pruebas unitarias.

**Resultados:**

- Las nueve pruebas implementadas finalizaron satisfactoriamente.
- Se identificó una incompatibilidad en la prueba de ejemplo incluida en el kit, debido al uso de atributos del starter, como `pending`.
- Esta limitación se encontraba contemplada en la documentación FAQ.

Se continuó con las validaciones utilizando las pruebas desarrolladas para la estrategia implementada.

### 2.10. Evaluación comparativa y selección de parámetros finales

Se ejecutó una evaluación adicional sobre el escenario de 150 000 tarjetas, considerando las siguientes combinaciones:

- `LAMBDA`: 8, 10 y 15.
- Retención: 0 y 15 minutos.

Tras comparar los resultados, se seleccionó la configuración:

**`LAMBDA = 10` y retención de 15 minutos.**

En comparación con la estrategia inicial, esta configuración presentó:

| Indicador | Variación |
|---|---:|
| Coste operativo | −7,7 % |
| Tiempo medio | −16 % |
| Percentil 95 (p95) | −3 % |
| RCP | Mejora registrada |

La configuración seleccionada permitió obtener un equilibrio más favorable entre coste, tiempo de procesamiento y desempeño operativo.

### 2.11. Mediciones de referencia

Se realizaron mediciones complementarias para establecer referencias de rendimiento.

La estrategia vacía registró aproximadamente 2,3 segundos de CPU en el escenario de 5 000 tarjetas, lo que permitió estimar el coste mínimo asociado a la comunicación con el entorno.

Asimismo, se comprobó que la estrategia inicial incumplía los criterios de evaluación en los cinco escenarios de estrés considerados.

### 2.12. Preparación y validación de la entrega

Finalmente, se trasladó la versión seleccionada de la estrategia al directorio `submission/`.

Se efectuaron ejecuciones de validación mediante los siguientes comandos:

```bash
python run.py --strategy submission/candidate_strategy.py
```

```bash
python run_150k.py
```

Se completó la documentación técnica en `STRATEGY.md` y se actualizó el archivo `submission_manifest.json`, dejando preparada la estructura de entrega.

---

## 3. Resultados generales de la sesión

Durante la sesión se completaron las actividades de preparación del entorno, análisis del motor, implementación de la estrategia, optimización de parámetros, pruebas de robustez y validación de la solución.

Los principales resultados fueron:

1. Implementación de una estrategia funcional para la asignación y despacho de tarjetas.
2. Eliminación de casos `UNRESOLVED` e infracciones de retención en los escenarios principales evaluados.
3. Reducción del coste operativo y del tiempo medio respecto de la estrategia inicial.
4. Validación mediante nueve pruebas unitarias y escenarios adicionales de estrés.
5. Identificación, documentación y corrección de dos incidencias durante el desarrollo.
6. Preparación de los archivos técnicos requeridos para la entrega.

## 4. Consideraciones sobre el uso de inteligencia artificial

Claude Code se utilizó como herramienta de asistencia técnica durante el análisis del código, la implementación de algoritmos, la generación de scripts, la ejecución de pruebas y la elaboración de documentación.

Las instrucciones fueron proporcionadas por el candidato y las operaciones ejecutadas por el asistente quedaron registradas cronológicamente.

Durante el proceso se identificaron instrucciones potencialmente incompatibles con las bases de la prueba, las cuales no fueron incorporadas a la solución.

Este registro tiene como finalidad proporcionar trazabilidad sobre las actividades desarrolladas con asistencia de inteligencia artificial, sin sustituir los resultados verificables de las ejecuciones ni la documentación técnica de la solución.

---

**Estado al cierre de la sesión:** Implementación y validaciones descritas completadas; estructura de entrega preparada.

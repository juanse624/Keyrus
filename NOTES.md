# NOTES

Notas de desarrollo en primera persona. Sin adornos, sin convertir lo que
pasó en anécdotas — esto es lo que ocurrió durante esta sesión de trabajo.

## Herramientas de IA usadas

Claude Code para la implementación, con revisión de cada plan antes de
ejecutarlo. Un asistente aparte para el análisis del dataset, la revisión de
planes y la verificación cruzada de cifras (ver "Verificación cruzada
independiente" más abajo).

## Algo que la IA hizo particularmente bien

Detectar que el sentinel `9999-12-31` de vigencia en el chart of accounts
revienta `pd.to_datetime` — Timestamp de pandas tiene rango de nanosegundos y
ese año está fuera de rango — y verificarlo corriendo código antes de
escribir la implementación, en vez de asumir que `pd.to_datetime` lo iba a
aceptar sin más. Está documentado en el propio código:
`src/finance_assistant/tools/accounts.py:135`. Si no se hubiera chequeado
antes de codear, el bug habría aparecido más tarde — probablemente como un
`OutOfBoundsDatetime` silencioso en un caso límite — y habría costado más
tiempo encontrarlo que evitarlo.

## Algo que hizo mal y cómo lo detecté

Tres episodios, todos con la misma forma: la observación de partida era
correcta, la interpretación que se construyó encima no.

**1. El fix que era una regresión.** Al construir el motor de política T&E,
se reportó como corrección que la subregla de registro de asistentes de
entretenimiento de cliente (`_eval_client_entertainment_attendee_record`,
`te_policy.py:448`) no debía disparar para importes por debajo de USD 500,
atándola al mismo umbral que gobierna la firma del VP. Se presentó como una
corrección, no como un cambio de comportamiento.

Era falso. `data/documents/travel_expense_policy.md:30-33` tiene dos frases
independientes bajo "Client entertainment": la primera lleva el umbral de
USD 500 y gobierna la firma del VP; la segunda — "The names and employers of
all attendees must be recorded" — no lleva umbral y es incondicional. El
comportamiento original (sin condicionar al umbral) era el correcto.

Lo detecté releyendo el documento fuente frase por frase en vez de aceptar
el resumen del modelo. Se revirtió, se agregó un test de regresión y un
comentario en `te_policy.py:453-454` dejando explícito que las dos
subreglas tienen alcances distintos, para que no se vuelvan a fusionar.

**2. Solape parcial tratado como confirmación.** El workflow de varianza
(`workflows/variance.py`) establece el driver numérico de la cuenta desde el
ledger y solo después busca una explicación documental — nunca al revés.
Reportó que el memo del board "confirmaba" la cuenta driver. No la
confirmaba: el driver real por importe era *Outbound Freight*, y el memo
discute *Expedited Freight* — una cuenta distinta y bastante menos material.
La única coincidencia era la palabra "freight".

Se endureció el chequeo (`variance.py:142-173`) para exigir que los
términos coincidentes cubran el conjunto completo de palabras del nombre de
la cuenta driver, no una intersección parcial, y se agregó un tercer estado,
`PARTIAL_OVERLAP`, con warning explícito. Contra los datos reales el
workflow resuelve hoy a `PARTIAL_OVERLAP`, con `sources` vacío — que es lo
correcto.

**3. El hueco correctamente detectado, cómodamente racionalizado.** Al
planificar el trace, se descubrió por cuenta propia que `ToolCall` nunca se
instanciaba en ningún workflow y que `bundle.tool_calls` quedaba vacío en
seis de las ocho preguntas. La observación era correcta. La conclusión no:
apoyándose en una instrucción mía ambigua ("reutiliza lo que el bundle ya
lleva"), se concluyó que un `steps[]` vacío era aceptable en esta fase, no
un defecto a corregir.

Un trace sin tool calls no cumple el entregable que el challenge pide de
forma explícita (`docs/PROMPT_MAESTRO.md`, sección TRACE). Se instrumentaron
los ocho workflows con un recorder centralizado (`ToolTrace`,
`workflows/_shared.py`) antes de construir `RunTrace`.

### Patrón común y qué lo detuvo

Los tres episodios son la misma familia: la observación es correcta y luego
se adopta la interpretación más cómoda — la que evita trabajo o la que
confirma lo que ya se había escrito. No es alucinación de datos, es sesgo de
conveniencia en la interpretación.

La contramedida que lo frenó fue procedimental, no de prompt: revisar cada
plan contra el documento fuente y contra el entregable del challenge tal
como está escrito, nunca contra el resumen del modelo ni contra la última
instrucción que yo había dado. En concreto, releer la política y el memo
directamente en vez de fiarme del resumen, y validar cada decisión de plan
contra lo que el challenge pide literalmente.

**4. Verificación cruzada independiente.** Los hallazgos de Fase A
(desfase posting/accrual, el hueco de FX, la doble vigencia del COA, las
claves repetidas de budget — ver apéndice) se calcularon por dos vías
independientes: el profiler del proyecto (`scripts/profile_data.py`) y un
análisis externo hecho aparte. Los números coincidieron exactamente,
incluidos los importes al céntimo. Cuando no coincidieron — el profiler
reportó 114 filas cruzando año calendario y el análisis externo 60 — la
discrepancia resultó ser una diferencia de definición (todas las cruces de
año frente a solo las del último ejercicio fiscal), no un error; el
desglose por par de años lo resolvió.

Ese mismo hábito de cuestionar un número que no cuadraba con una expectativa
calculada por fuera fue lo que destapó un bug real: la asunción de "año por
defecto" (cuando una pregunta no especifica año) se filtraba dentro de
bundles `NEEDS_CLARIFICATION` en los que, por definición, no se había
aplicado ningún default — en dos módulos distintos. Los tests que existían
en ese momento no lo cazaban.

**5. El mock validaba mi contrato, no el del proveedor.** Toda la capa de
orquestación (`orchestration/`) se construyó y testeó contra un cliente LLM
simulado — `fake_llm_client` en los tests, nunca una llamada real. Los 340
tests pasaban, incluidos los de `interpreter.py` y `orchestrator.py`. La
primera vez que se corrió contra un proveedor real (Groq, `groq/llama-3.3-70b-versatile`)
aparecieron dos bugs que ningún test cazaba, porque ningún test tocaba la
superficie donde vivían.

El primero: `litellm.completion_cost()` se llamaba sin `model=` explícito.
`response.model` — lo que el objeto de respuesta trae de vuelta — es el
nombre crudo del modelo tal como lo devuelve el proveedor, sin el prefijo
(`"groq/..."`) que la tabla de precios de litellm necesita para resolverlo.
La resolución fallaba, la excepción se tragaba (`except Exception: cost =
"unknown"`), y el costo quedaba siempre en `"unknown"` — no un valor
incorrecto, simplemente nunca el real. Se corrigió pasando `model=` de
forma explícita a `completion_cost()` en vez de dejar que dependiera del
eco del proveedor (`interpreter.py:126-133`).

El segundo, más grave: la validación de esquema de tool-calling de Groq
rechaza la llamada de plano cuando el modelo omite del JSON un campo
opcional con valor nulo, aun cuando el schema de Pydantic lo declara
nullable — un `litellm.BadRequestError` en tiempo de llamada, no un error
de parseo después. Esto rompía 3 de las 8 preguntas, que volvían como
`UNKNOWN` con `status=ERROR` en vez de resolver el intent real. Se
resolvió con un reintento explícito: al capturar ese `BadRequestError`
puntual, la segunda llamada pide JSON plano (`response_format={"type":
"json_object"}`) con el schema escrito en el prompt en vez de impuesto
server-side — `IntentRequest` ya tolera campos opcionales ausentes al
parsear, así que el reintento degrada con gracia en vez de propagar el
fallo (`interpreter.py:106-123`).

La lección, escrita sin dramatismo: el cliente simulado validaba mi propio
contrato, no el del proveedor. Un mock confirma que el código hace lo que
yo creo que debe hacer; no confirma que el mundo se comporte como yo creo.
Por eso conseguí una credencial real en vez de dar la capa por verificada
con 340 tests en verde.

## Qué recorté

`canonical_vendor_id` / merge real de vendors. `detect_alias_clusters`
(`tools/vendors.py`) detecta candidatos de fusión por normalización mecánica
del nombre, pero nunca los resuelve a un mapeo canónico — eso es correcto
según R6 (nunca fusionar por similitud de nombre sin un mapeo autoritativo),
pero además es, explícitamente, funcionalidad que quedó fuera del alcance
entregado: no existe ningún flujo — humano o automático — para promover un
cluster candidato a una identidad de vendor consolidada.

La UI de Streamlit tampoco se construyó — `docs/PROMPT_MAESTRO.md` la
sugiere como capa opcional sobre `answer_question`, y el CLI (modo JSON y
modo texto libre) quedó como la única interfaz entregada. El Answer
Renderer opcional (prosa desde un `EvidenceBundle` ya decidido, vía LLM)
tampoco se implementó; `render_bundle_text` sigue siendo el único
renderer, y es determinista.

## Tiempo invertido

Día y medio, aproximadamente 10-12 horas. Consistente con los timestamps de
los commits: el primero el 2026-08-07 a las 17:53, el último el 2026-08-08 a
las 19:30.

## Con dos días más

Cinco direcciones, en orden de lo que reduciría más riesgo primero:

Medir coste real por tool y por llamada a modelo, con presupuesto de costo
por pregunta, no solo el techo de número de llamadas que ya existe
(`LLM_MAX_COST_USD_PER_QUESTION` acota el total, pero no distingue qué
componente de una pregunta compuesta se lo está comiendo). Un catálogo
semántico versionado para las convenciones no escritas del dominio — base
de devengo, perímetros de opex, reglas de restatement de cost-centre — en
vez de la configuración dispersa entre `config.py`, `config/policy_rules.yaml`
y los defaults de cada workflow que existe hoy. Ampliar el eval set más
allá de las ocho preguntas del challenge: casos adversariales que intenten
inducir una respuesta segura donde no la hay, y un dataset sintético con
anomalías distintas — no las mismas 894/114/228/864 filas de este dataset —
para confirmar que la detección de R1-R7 es genérica de verdad y no quedó
calibrada, sin darme cuenta, a las particularidades de este fixture.
Resolución de identidad de proveedor con un maestro canónico y un proceso
de aprobación humana explícito, en vez de dejar `detect_alias_clusters`
como el techo funcional (ver "Qué recorté"). Y observabilidad sobre la
distribución de estados (`ANSWER`/`PARTIAL`/`REFUSED`/`NEEDS_CLARIFICATION`)
a lo largo del tiempo, para detectar si el sistema empieza a rechazar más o
menos de lo esperado — la clase de señal que ni un test ni un eval
puntual puede dar, porque depende de la distribución real de preguntas que
llegan, no de un caso fijo.

---

## Apéndice: invariantes y anomalías detectadas (Fase A)

Output real de `python scripts/profile_data.py` corrido contra `data/` (Fase A,
generado automáticamente, no editado a mano). El script es genérico: detecta
estas anomalías comparando datasets entre sí, no las asume ni las hardcodea.

```
## Rangos de fecha (GL)
- posting_date: 2023-01-01 .. 2025-01-14
- accrual_date: 2023-01-01 .. 2024-12-31

## Filas donde el mes de posting_date difiere del de accrual_date
- 894 filas (8.19% del GL)
- muestra:
 txn_id posting_date accrual_date
T000024   2023-02-01   2023-01-30
T000034   2023-02-01   2023-01-31
T000044   2023-02-01   2023-01-30
T001254   2023-02-01   2023-01-31
T003620   2023-02-01   2023-01-29
### Desglose: filas donde el AÑO de posting_date difiere del de accrual_date
- 114 filas (1.04% del GL)
- importe agregado por moneda (local, sin convertir):
          filas  importe_local
currency                      
CAD          24      205394.21
EUR          38      306745.82
USD          52      535585.10

## Combinaciones (mes, moneda) en GL (base accrual_date) ausentes en fx_rates
- 1 combinaciones faltantes de 72 esperadas
  - 2024-09 / EUR

## account_codes con más de una fila de vigencia en el COA
- 1 account_codes con múltiples filas
  - 6230: 2 filas

## Claves dimensionales repetidas en budget (entity+cost_centre+account_code+period_month)
- 228 claves repetidas, 456 filas involucradas
  - ('MI-US', 'OPS-AMER', '6110', '2024-01'): 2 filas
  - ('MI-US', 'OPS-AMER', '6110', '2024-02'): 2 filas
  - ('MI-US', 'OPS-AMER', '6110', '2024-03'): 2 filas
  - ('MI-US', 'OPS-AMER', '6110', '2024-04'): 2 filas
  - ('MI-US', 'OPS-AMER', '6110', '2024-05'): 2 filas
  - ('MI-US', 'OPS-AMER', '6110', '2024-06'): 2 filas
  - ('MI-US', 'OPS-AMER', '6110', '2024-07'): 2 filas
  - ('MI-US', 'OPS-AMER', '6110', '2024-08'): 2 filas
  - ('MI-US', 'OPS-AMER', '6110', '2024-09'): 2 filas
  - ('MI-US', 'OPS-AMER', '6110', '2024-10'): 2 filas
  - ... y 218 claves más

## Filas del GL sin vendor_id
- 864 filas (7.91% del GL)
```

### Lectura de estos hallazgos frente a las reglas de `docs/PROMPT_MAESTRO.md`

- **R1 (base temporal)**: confirma el fixture esperado — 894 filas devengadas
  en un mes y contabilizadas en otro; de esas, 114 (1.04% del GL) incluso
  cruzan el límite de **año** (`posting_date.year != accrual_date.year`),
  con importe agregado por moneda: CAD 205,394.21 / EUR 306,745.82 /
  USD 535,585.10 (24/38/52 filas respectivamente, sin convertir). Esto es
  exactamente el fixture que R1 describe ("deben existir filas con
  `posting_date.year > accrual_date.year`"). Un filtro de FY2024 por
  `posting_date` en vez de `accrual_date` clasificaría mal estas 114 filas
  en el año equivocado — de ahí que `accrual_date` sea la base por defecto.
- **R2 (FX)**: exactamente una combinación (mes, moneda) falta —
  `2024-09 / EUR` — coincide con el fixture descrito ("falta exactamente una
  combinación moneda/mes"). Cualquier agregación en USD que toque ese mes/
  moneda debe reportar cobertura < 100%, nunca ocultar las filas.
- **R3 (COA temporal)**: el `account_code` `6230` tiene 2 filas de vigencia
  — confirma que existe al menos una cuenta con cambio de padre/atributos a
  mitad de periodo. El join a cuentas debe ser temporal (`valid_from`/
  `valid_to`), nunca `drop_duplicates("account_code")`.
- **R5 (budget)**: 228 claves dimensionales repetidas (456 filas) en
  `budget.csv`. Confirma que `drop_duplicates()` ciego destruiría información
  real; se requiere una regla de agregación declarada y justificada (fase
  posterior).
- **R6 (vendors)**: 864 filas de GL (7.91%) sin `vendor_id`. Confirma que un
  `inner join` de vendors borraría gasto legítimo (p. ej. nómina); el join
  debe ser `left`.

Estos números son específicos de *este* dataset y se citan aquí solo como
evidencia de que el script los encontró correctamente — el código de
`scripts/profile_data.py` y de las tools de fases posteriores nunca los
hardcodea.

# ARCHITECTURE

**The system is agentic at the interpretation boundary and deterministic at
the financial-computation boundary.**

> "The LLM understands the question. Deterministic tools establish the
> facts. The orchestrator decides whether those facts are sufficient to
> answer." — `docs/PROMPT_MAESTRO.md`

## Las tools y por qué separadas así

Cada tool en `src/finance_assistant/tools/` implementa exactamente una regla
de `docs/PROMPT_MAESTRO.md` (R1-R8), como función pura de dataclasses —
nunca pydantic, nunca estado compartido:

| Tool | Regla | Responsabilidad | Modo de fallo que evita |
|---|---|---|---|
| `query_ledger` | R1 | Filtra el ledger sobre un campo de fecha financiera configurable (default `accrual_date`) | Filtrar por `posting_date` en vez de `accrual_date` clasifica 114 filas en el año contable equivocado (ver apéndice de `NOTES.md`) |
| `resolve_account_hierarchy` | R3 | Join temporal GL↔COA por `account_code` + vigencia en la fecha financiera de cada fila | Un `drop_duplicates("account_code")` o un join no temporal mapea filas contra la vigencia de cuenta equivocada cuando una cuenta cambió de padre a mitad de periodo (cuenta `6230`) |
| `convert_to_usd` / `aggregate_usd(_by)` | R2 | Conversión FX por (mes, moneda); cobertura como estructura de datos, nunca implícita | Un groupby que ignora NaN reporta un centro de costo por debajo de plan cuando en realidad faltan filas por convertir, no que gastó menos |
| `query_budget` | R5 | Detecta claves dimensionales repetidas, nunca `drop_duplicates()` ciego | `drop_duplicates()` ciego descarta 456 filas reales de `budget.csv` en silencio, subestimando el plan en 228 combinaciones dimensionales |
| `normalize_reporting_cost_centre` | R4 | Aplica la transición de cost-centre declarada en config, preservando el origen | Comparar el cost-centre pre-transición contra el post-transición sin normalizar reporta una varianza que es solo el renombre del centro, no gasto real |
| `search_documents` | R8 | Búsqueda determinista por palabra clave/sección, sin vector DB | Un substring match sin límite de palabra ("office" adentro de "Officer") o buscar los 4 documentos por cada pregunta rompe la trazabilidad de qué evidencia sustenta realmente una afirmación |
| `vendor_lookup` / `detect_alias_clusters` | R6 | Left join (nunca inner); detecta clusters de alias sin aplicarlos | Un inner join borra 864 filas de gasto legítimo sin `vendor_id` (p. ej. nómina); fusionar alias automáticamente por similitud de nombre puede unir dos proveedores distintos bajo un ranking falso |
| `detect_duplicate_candidates` | R7 | Fingerprint económico + anotación de reversals | Tratar cualquier par con mismo importe/fecha/vendor como duplicado sin chequear reversals convierte una nota de crédito legítima en una acusación de pago doble |
| `evaluate_te_policy` | — | Motor de reglas T&E parametrizado desde `config/policy_rules.yaml` | Hardcodear un umbral en código en vez de leerlo de `policy_rules.yaml` con cita de sección esconde de dónde sale la regla y la desincroniza en silencio si la política cambia |

Están separadas así porque cada una encapsula una decisión de diseño no
obvia (una regla R1-R8) que necesita su propio test de caja negra,
independiente de las demás. Un `workflow/` compone varias tools en un plan
fijo por pregunta; el modelo nunca llama a una tool directamente — no son
"function-calling tools" expuestas a un LLM, son funciones Python que un
workflow determinista invoca.

## Qué decide el modelo

Por diseño (`docs/PROMPT_MAESTRO.md`, sección ORQUESTACIÓN), el modelo tiene
exactamente dos responsabilidades, ambas acotadas y ninguna involucra
aritmética ni evidencia:

1. **Question Interpreter** — una llamada, salida estructurada
   (`IntentRequest` tipado): a qué `Intent` de los 8 pertenece la pregunta y
   con qué parámetros. Confianza baja → `NEEDS_CLARIFICATION`, nunca una
   adivinanza.
2. **Answer Renderer** (opcional) — convierte un `EvidenceBundle` ya
   decidido en prosa. Recibe el bundle completo, no puede agregar hechos ni
   cambiar el `status`.

**Estado de esta capa**: el Question Interpreter está construido y
verificado contra un proveedor real
(`orchestration/{intents,interpreter,plans,orchestrator}.py`, Fase H).
`orchestration.interpreter.interpret_with_llm` hace la única llamada al
LLM, con salida estructurada `IntentRequest` vía litellm; sin credencial
disponible, `orchestration.orchestrator.answer_question` cae
automáticamente a `interpret_with_keywords` (determinista, por palabras
clave) y lo declara en `assumptions` — las ocho preguntas se siguen
respondiendo sin credencial, demostrado en
`tests/test_orchestration_orchestrator.py`. `orchestration.plans` resuelve
el intent a un workflow (registro determinista, `Intent -> IntentSpec`) y
completa los parámetros que el modelo nunca puede conocer (años/fechas del
dataset) antes de ejecutarlo. `evidence/render.py::render_bundle_text`
sigue siendo el único renderer — el Answer Renderer (opcional, prosa desde
un `EvidenceBundle` ya decidido) queda fuera del alcance de esta fase y
sigue pendiente. La capa se corrió una vez contra un proveedor real (Groq)
para verificarla más allá de un cliente LLM simulado; los dos bugs reales
que esa corrida destapó, y por qué ningún test los cazaba, están narrados
en `NOTES.md`.

## Qué es determinista

Todo lo demás. Joins temporales, conversión y agregación FX, la tabla de
degradación del Evidence Gate, el cálculo de cobertura, la construcción del
trace, y el renderer de texto. Ningún número que aparece en un
`EvidenceBundle` pasó por un LLM.

## Por qué la frontera está ahí

El LLM se acota a interpretar lenguaje natural y, opcionalmente, a
enunciar en prosa un resultado ya fijado. Todo lo que toca números,
umbrales o suficiencia de evidencia es Python puro. La consecuencia directa:
esa parte es testeable, reproducible byte a byte, y auditable sin volver a
invocar un LLM — correr el mismo `EvidenceBundle` por el renderer produce
siempre el mismo texto; correr el mismo input por un LLM no.

## Dónde deliberadamente NO se usó un agente

- Los `workflows/` son planes fijos por intent — funciones, no un loop de
  agente decidiendo dinámicamente qué tool llamar a continuación. Cada una
  de las 8 preguntas tiene una secuencia de pasos escrita de antemano.
- `search_documents` no tiene ningún default de `filenames` — todo caller
  debe declarar explícitamente qué documento(s) busca. No existe ningún
  camino de código que busque los 4 documentos para cada pregunta "por si
  acaso".
- `evaluate_te_policy` y `detect_duplicate_candidates` devuelven candidatos
  con estados tipados (`CONFIRMED_RULE_MATCH` / `POTENTIAL_BREACH` /
  `INSUFFICIENT_EVIDENCE` / ... y `HIGH` / `MEDIUM` / `LOW` de confianza),
  nunca un veredicto. Ninguna decisión de juicio se presenta como hecho
  establecido.

## Cómo se manejan datos faltantes y rechazos

`evidence/gate.py` aplica una tabla de degradación determinista con
severidad `REFUSED > NEEDS_CLARIFICATION > PARTIAL > ANSWER`; el modelo no
participa en esta decisión. Una tasa FX faltante nunca se interpola: se
propaga como `MissingFXRate` estructurado (moneda, mes, filas afectadas,
monto local afectado). Cobertura menor al 100% nunca produce `ANSWER`.

Ejemplo real, del trace committeado
`traces/samples/20260809T003022Z_consolidated_spend_dd56ecac.json` (Q3, "consolidated
spend in USD"): de 1.348 filas seleccionadas para el trimestre, 1.201 son
convertibles (89.09%) porque falta la tasa EUR de 2024-09 (147 filas, USD
1.231.309,12 en moneda local sin convertir). El bundle resultante tiene
`status: REFUSED` y `result: null` — el total exacto genuinamente no existe
— pero **no** es un rechazo mudo: `calculations` trae el total USD
computable sobre las filas convertibles (10.003.879,95), el desglose por
entidad (MI-CA 1.990.231,32 / MI-NL 3.134.109,40 / MI-US 4.879.539,23), y el
monto local no convertible por moneda. El analista se lleva el 89% del
cuadro con total claridad sobre qué falta y por qué. (Ver "Discrepancia
argumentada" más abajo — esto no es incidental, es la consecuencia directa
de un desacuerdo de diseño explícito.)

## Cómo se rastrea la evidencia

`EvidenceBundle` (el modelo pydantic central) se construye acumulando
`ToolCall` y `CalcStep` a medida que un workflow ejecuta. La instrumentación
pasa por un único punto: la clase `ToolTrace` en `workflows/_shared.py`
envuelve cada llamada a una tool, hace bind genérico de argumentos por firma
(`inspect.signature`), mide tiempo, y resume ambos lados de la llamada vía
`evidence/summarize.py`. `evidence/trace.py::build_trace` proyecta
`tool_calls` + `calculations` del bundle ya terminado a `steps[]` — nunca
una segunda vía de instrumentación — y `write_trace` serializa a
`traces/<run_id>.json`.

`summarize.py` distingue dos funciones a propósito:
`summarize_for_trace` (detalle completo, recursivo) para el *resultado* de
una llamada, y `summarize_argument_for_trace` (colapsa un argumento que es
una dataclass a una referencia compacta de una línea, p. ej.
`<FxConversionResult: 1348 rows, convertible_rows=1201, ...>`) para sus
*argumentos*. La distinción existe porque, sin ella, pasar el
`FxConversionResult` que `convert_to_usd` acaba de devolver directo a
`aggregate_usd`/`aggregate_usd_by` duplicaba en el trace toda la estructura
de cobertura que el paso anterior ya había mostrado en detalle como su
propio resultado — un bug real, corregido en el commit `c6c752e` junto con
un `repr()` feo de tuple keys de groupby (`('MI-CA',)` en vez de `MI-CA`).

## Cómo se acotan loops y coste

`.env.example` define `LLM_MAX_CALLS_PER_QUESTION` (default 5) y
`LLM_MAX_COST_USD_PER_QUESTION` (default 0.50) como ceilings explícitos que
`orchestration.orchestrator._check_ceilings` aplica en código, inmediatamente
después de la llamada al intérprete y antes de resolver el plan — nunca
dentro de la propia llamada LLM. Exceder cualquiera de los dos produce un
`EvidenceBundle` con `status=ERROR` y la razón en `warnings` (nunca
`refusal_reason`, reservado para `REFUSED`); el workflow correspondiente
nunca llega a correr. El costo se suma solo sobre las llamadas con costo
conocido — si `litellm.completion_cost` no pudo resolver el precio del
modelo (ver el bug de `completion_cost` sin `model=` explícito en
`NOTES.md`), esa llamada aporta `"unknown"` al total, no cero, y se declara
explícitamente en `assumptions` ("model pricing unknown ... cost ceiling
could not be fully enforced") en vez de fingir que el ceiling se aplicó
completo. Con una sola llamada por pregunta (el Question Interpreter nunca
hace más de una), el costo real por pregunta corrido contra Groq queda muy
por debajo de ambos ceilings — el propósito de los ceilings en esta fase es
la protección estructural, no un límite ajustado al costo observado.

En la capa determinista, cada workflow es una secuencia fija de llamadas a
tools, sin recursión ni selección abierta de próxima acción — no hay
superficie de loop abierto en esa capa, con o sin orquestador encima.

## Por qué se rechazó ReAct abierto

Las ocho preguntas analíticas del challenge son un conjunto cerrado y
enumerable de rutas — el espacio de intents se conoce por completo de
antemano. Un plan fijo por intent (los `workflows/`) da exactamente la
misma cobertura funcional que un agente ReAct con selección abierta de
tools en cada paso, pero sin tres riesgos que ReAct sí introduce: (1) una
secuencia de tools incorrecta elegida en tiempo real, (2) la necesidad de un
scratchpad y lógica de auto-corrección cuando el agente se equivoca de
tool, y (3) coste y latencia no deterministas por pregunta. A cambio, se
gana reproducibilidad total — el mismo input produce siempre los mismos
pasos — y cada plan es un test unitario, no una política aprendida. En un
dominio donde la corrección financiera es el criterio de éxito, esa
garantía vale más que la flexibilidad que ReAct ofrece para preguntas que,
en este challenge, no van a aparecer.

## Discrepancia argumentada

`docs/PROMPT_MAESTRO.md` invita explícitamente a discrepar de alguno de sus
propios principios. El que discuto es:

> "A plausible wrong answer costs more than a refusal."

Comparto la dirección — preferir un rechazo transparente a una respuesta
plausible pero no sustentada es correcto — pero la formulación es
incompleta, y este proyecto lo demuestra. La dicotomía que plantea opone
"respuesta plausible-pero-errónea" contra "rechazo", como si fueran las
únicas dos categorías relevantes. No lo son: la categoría que de verdad
importa es una tercera — el rechazo que además entrega lo que sí se pudo
establecer, frente al rechazo que no entrega nada.

La evidencia está en el propio repo. En Q3 (arriba) falta la tasa EUR de
2024-09, así que el total consolidado exacto no es defendible y el sistema
lo rechaza — correcto. Pero un rechazo seco habría tirado a la basura
información perfectamente sólida: 1.201 de 1.348 filas eran convertibles,
los componentes por entidad estaban calculados, y el importe local no
convertible estaba cuantificado con precisión. El bundle devuelve
`result: null` — el total exacto genuinamente no existe — y a la vez
entrega esos tres bloques en `calculations`. El analista se lleva el 89%
del cuadro y sabe exactamente qué le falta y por qué.

De ahí sale una consecuencia de diseño que el principio original no
menciona: **el estado del bundle y el contenido del bundle son ejes
independientes.** Un bundle `REFUSED` puede llevar más información útil que
un `ANSWER` pobre. Si se colapsan los dos ejes — si "rechazar" se entiende
como "no decir nada" — el sistema aprende a callarse precisamente cuando
debería estar acotando lo que sabe. La prueba de que esto no es solo
retórica está en `evals/questions.yaml`: el caso de Q3 no solo asevera que
`status == REFUSED`, también asevera que `calculations` trae los
componentes computables. Una regresión que dejara el rechazo mudo pasaría
el primer assert y fallaría el segundo — el eval está diseñado para cazar
exactamente el error de colapsar los dos ejes.

Reformulación que propondría en su lugar:

> "An unsupported answer costs more than a refusal — and a refusal that
> discards what could be established costs more than one that reports it."

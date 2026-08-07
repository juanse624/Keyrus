# NOTES

## Invariantes y anomalías detectadas

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

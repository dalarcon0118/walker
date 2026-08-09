# ADR-WALKER-021: BDD como metodología de desarrollo para el módulo CPG

> **Status:** Accepted
> **Fecha:** 2026-08-06
> **Contexto:** los tests del módulo CPG estaban dispersos en test_migrated_gaps.py (clases unitarias sueltas) y solo 4 escenarios BDD cubrían el result pattern.
> **Complementa:** ADR-WALKER-016 (Bounded Context Protocol), ADR-WALKER-017 (build-time vs query-time)

---

## 1. Contexto y problema

Los tests de `features/code_analysis/` (módulo CPG) estaban repartidos entre:

- `tests/test_cpg_result_pattern.py` — único archivo BDD (4 escenarios, result pattern)
- `tests/test_migrated_gaps.py` — 9 clases unitarias de CPG mezcladas con spring/verify/mcp-config
  (TestHallucination, TestMetricsCache, TestCallsCache, TestScanDfg, TestBuildServiceResultPattern,
  TestCpgPerformance, TestCpgProjectIntegration, TestCpgUseCases, TestCheckConventions)

**Problemas**:
1. Cobertura fragmentada: cada área del CPG (build, use cases, caches, convenciones, hallucination,
   quality gates, performance) se testea con estilos distintos.
2. Los tests unitarios sueltos no expresan el **contrato** — describen la implementación, no el comportamiento.
3. No hay una especificación ejecutable que guíe el desarrollo (los tests no "dicen" qué debe hacer el sistema).

### El principio rector

> **Los tests son la especificación.** Primero se escribe el comportamiento esperado en lenguaje de
> negocio (Gherkin: Dado/Cuando/Entonces), luego se implementa lo mínimo para que pase (ciclo RED→GREEN→REFACT).
> El código se ajusta al contrato, no al revés.

---

## 2. La decisión

**Adoptar BDD (pytest-bdd) como metodología de los tests del módulo CPG**: reescribir TODA la cobertura
del módulo como features Gherkin + step files, extraer las clases CPG de `test_migrated_gaps.py`
conservando el resto, y que los `.feature` sean el SSOT del comportamiento esperado.

### El patrón objetivo

```
tests/features/cpg_*.feature      ← especificación ejecutable (Gherkin en español)
tests/test_cpg_*.py               ← step files (pytest-bdd), ejercitan cores reales
tests/test_migrated_gaps.py       ← solo tests NO-CPG (spring, verify, engine, mcp-config)
```

### Features creados (9 features / 29 escenarios)

| Feature | Escenarios | Contrato |
|---|---|---|
| `cpg_result_pattern.feature` | 4 | Result pattern (Success/Failure, @safe) |
| `cpg_conventions.feature` | 2 | CKG → ast-grep (violaciones, fallback repo irresoluble) |
| `cpg_use_cases.feature` | 7 | Health, Gather, DetectUnused, Review, Metrics |
| `cpg_project_integration.feature` | 3 | Cadena pública ProjectService → CPGBuilder (ADR-015) |
| `cpg_hallucination.feature` | 2 | Imports alucinados + stdlib |
| `cpg_infra_caches.feature` | 5 | metrics_cache, calls_cache, scan_dfg (+async), expand_paths |
| `cpg_build_result.feature` | 2 | _sync_core @safe + log no silencioso |
| `cpg_quality_gates.feature` | 3 | Envelope side_effect + dispatch quality_gate_failed |
| `cpg_performance.feature` | 1 | Update incremental < build completo |

### Por qué es correcto

| Argumento | Detalle |
|---|---|
| **Tests = especificación viva** | El `.feature` es a la vez documento y test: leíble por PO/QA/dev y ejecutable |
| **Guía el desarrollo** | Se escribe el contrato antes del código (RED→GREEN); el código mínimo que cumple el escenario |
| **Red de seguridad al refactorizar** | Si los 29 escenarios pasan, el comportamiento no cambió |
| **Extracción limpia** | test_migrated_gaps queda con 13 clases no-CPG (task_synthesis, ceremonies, verify, engine, mcp) |
| **Resultado verificable** | Suite completa: 295 passed |

### Qué NO cambia

- **El motor de tests sigue siendo pytest** (pytest-bdd como runner de los `.feature`).
- **Los cores @safe y result pattern** (convention-001) siguen siendo el estilo de código.
- **test_migrated_gaps.py se conserva** para los módulos no-CPG.
- **Markers registrados** en `pyproject.toml` (contrato, fallback, contexto, bugs, review, metrics,
  regresion, stdlib, cache, gates, rendimiento) — sin warnings.

---

## 3. Cambios de diseño

### 3.1 Estructura de tests

```
tests/
  features/           ← .feature Gherkin (SSOT del comportamiento)
  test_cpg_*.py       ← step files pytest-bdd (9 archivos)
  test_migrated_gaps.py ← 13 clases no-CPG conservadas
```

### 3.2 Convención de steps

- Cada escenario usa fixtures aisladas en directorios temporales (determinismo) **o** la cadena pública
  con el proyecto real 'walker' cuando el contrato exige integración (review, metrics, conventions).
- Los steps ejercitan los **cores reales** (build_service._sync_core, conventions.execute,
  IncrementalMetricsCache, etc.), no mocks.

---

## 4. Impacto

| Área | Antes | Después |
|---|---|---|
| Tests del módulo CPG | 9 clases sueltas + 4 BDD | 29 escenarios BDD en 9 features |
| test_migrated_gaps.py | 1963 líneas (mezclado) | 1269 líneas (solo no-CPG) |
| Metodología | tests después del código | tests primero (contrato) |
| Suite completa | 294 passed | 295 passed |

### Trade-off aceptado

**Costo de escritura inicial mayor**: definir bien los escenarios Gherkin toma más tiempo que
"hacer un assert rápido". Se acepta porque el `.feature` queda como documentación viva del contrato.

---

## 5. Alternativas consideradas

| Alternativa | Por qué se rechaza |
|---|---|
| **Conservar tests unitarios sueltos** | No expresan el contrato ni guían el desarrollo |
| **Un solo feature gigante** | Ilegible y sin separación de dominios (build vs caches vs quality gates) |
| **Solo documentación .md** | No es ejecutable: se desactualiza |

---

## 6. Pasos de implementación

1. Crear los 9 `.feature` en `tests/features/`.
2. Crear los 9 step files `tests/test_cpg_*.py` (pytest-bdd).
3. Extraer las 9 clases CPG de `test_migrated_gaps.py` (quedan 13 no-CPG).
4. Registrar los markers en `pyproject.toml`.
5. Verificar suite completa (295 passed).

---

## 7. Reglas de diseño (non-negotiables)

- **Los tests del módulo CPG son BDD** (`.feature` + steps) — no unit tests sueltos.
- **Los escenarios describen el contrato**, no la implementación.
- **Nunca `except: pass`** en tests ni en steps (convention-001).
- **El `.feature` es el SSOT del comportamiento**: si el comportamiento cambia, cambia el feature primero.

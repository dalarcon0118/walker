# ADR-WALKER-052: Gate DAG de arranque y resolución barata de ciclos feature-level (dishka diferido)

> **Status:** Propuesta
> **Fecha:** 2026-08-24
> **Autor:** Kilo (agente) + PO
> **Complementa:** ADR-WALKER-045/046 (composition root + AppContainer), ADR-WALKER-051 (gaps refactor), `capa_rout.md` D1

---

## 1. Problema

### 1.1 Evidencia del día (2026-08-24)

La sesión de depuración del hot-reload MCP (-32001 / `'TaskResult' object has no attribute 'unwrap'`) dejó dos aprendizajes estructurales:

1. **La causa raíz fue la recarga parcial en orden arbitrario**, no la ausencia de un framework DI. El hot-reload topológico (grafo AST + cierre de importadores + Kahn) eliminó el hazard `isinstance` entre identidades viejas/nuevas.
2. **Tarjan sobre el grafo real** (`features.*` cargados) detectó 4 SCCs:

| SCC | Módulos | Estado |
|---|---|---|
| composition core | main/container/registry/handlers/app/front_controller/lifecycle/composition.container (8) | ✅ RESUELTO (PEP 562 + imports SSOT + inyección dinámica — ver tests `test_import_acyclicity.py`) |
| delta ↔ project | spring_service ↔ ProjectService/use_cases/update_project (7) | ⏳ pendiente |
| code_analysis ↔ cpg_service | package init hub (2) | ⏳ pendiente |
| (fantomárgico) container ↔ lifecycle | aristas bajo `if TYPE_CHECKING:` | ✅ resuelto excluyendo TYPE_CHECKING del grafo estático |

### 1.2 Evaluación de frameworks DI (dishka y alternativas)

Se investigó adoptar un framework (dishka v1.9.1, dependency-injector 4.49, modern-di, nestdi; frameworks web completos Ellar/Nestipy/PyNest descartados por ser ASGI-first):

- **dishka aporta**: `Provider` clases modulares ≈ FeatureModule, `Component`s ≈ fronteras de feature, scopes, resolución lazy (= forwardRef).
- **No aporta**: validación del grafo de IMPORTS entre módulos al arranque (su resolución es lazy — un ciclo explota en el primer `.get()`, no en el boot). Nuestro requisito central (fallar temprano con nombres de módulos) seguiría siendo código propio.
- **Fricción concreta**: resolución type-based vs tokens string de walker; proveedores que devuelven no-clases (dicts de handlers, metadata de routes); hot-reload obliga a reconstruir el container por cascada igual que hoy (cero ganancia ahí).
- **Costo de migración**: concentrado en `composition.registry` — EL archivo más acoplado (10 deps externas), con sprint L2 activo y sesión agente paralela escribiendo el repo.

**Conclusión costo/beneficio**: el dolor real (recargas, recomposición oculta, anclaje multi-proyecto) ya fue resuelto quirúrgicamente sin framework. Adoptar uno ahora compra pulido, no capacidad.

---

## 2. Decisión

Tres decisiones acotadas, en este orden, para NO desviar el foco del sprint L2:

### D1 — Gate DAG de arranque (reutilizar Tarjan, ~40 líneas)

✅ IMPLEMENTADO (2026-08-24)

Subir el contrato de `tests/test_import_acyclicity.py` a **runtime**: `AppContainer.build()` valida que su propio grafo de composición sea un DAG antes de registrar. Si encuentra un SCC > 1, **falla el arranque** listando los módulos del ciclo y la regla rota:

```python
# features/main/composition/container.py (esbozo)
def _validate_dag_or_fail(loaded: dict[str, ModuleType]) -> None:
    graph = {n: static_deps(n, m.__file__) for n, m in loaded.items()}  # AST
    cyclic = [m for scc in tarjan(graph) if len(scc) > 1 for m in scc]
    if cyclic:
        raise CompositionCycleError(
            f"ciclo de imports en el composition core: {sorted(cyclic)}. "
            "Regla: exports perezosos (PEP 562), imports SSOT a módulos de "
            "definición, o resolución dinámica en factories DI. "
            "Excepciones temporales: ADR-WALKER-052 §2.2."
        )
```

- Reusa `_module_deps` de `reloader.py` como SSOT de aristas (relativos, absolutos, exclusión TYPE_CHECKING) — cero duplicación.
- El grafo se calcula UNA vez por build (post-recarga también), no por request.
- Escape hatch: allowlist explícita de excepciones temporales EN ESTE ADR (§2.2), nunca en silencio.

**Resultado**: `AppContainer.build()` falla con `CompositionCycleError` si existe un ciclo, con diagnóstico accionable.

### D2 — Resolver los 2 ciclos restantes con el playbook barato

✅ IMPLEMENTADO (2026-08-24)

Mismo mecanismo ya validado en el ciclo principal, hojas primero:

| Orden | Ciclo | Corte aplicado |
|---|---|---|
| 1º | `code_analysis ↔ cpg_service` | Resuelto previamente via PEP 562 en `__init__.py` del paquete |
| 2º | `delta_tracking ↔ project` (7 módulos) | Importaciones directas movidas a resolución dinámica via `resolve_service` en `core.engine.bootstrap` |

**Criterio de aceptación**: `test_composition_core_sin_ciclos` extendido a TODOS los SCCs = vacío (no solo composition core).

**Resultado**: DAG limpio, gate D1 pasa sin ciclos.

### D3 — dishka DEFERIDO con disparadores explícitos

No se adopta ningún framework DI ahora. Se reconsiderará SOLO si se cumple alguno:

| Disparador | Umbral |
|---|---|
| Sprawl de factories en `register_all()` | > 30 proveedores |
| Servicios request-scoped proliferando | ≥ 3 contextos vivos por-request |
| Segundo runtime con wiring distinto | server + workers + plugins externos |

Mientras tanto, nos inspiramos en sus APIs (Provider modular, Components, lazy resolution) sin pagar su costo.

**Estado actual**: No se cumple ningún disparador. El composition root maneja ~10 proveedores sin proliferación de request-scoped services.

### 2.2 Allowlist de excepciones temporales

(vacía al momento de aprobar; toda entrada nueva debe citar issue/sprint y fecha de caducidad)

---

## 3. Escenarios de fallo

| # | Fallo | Causa raíz | Mitigación | Detección |
|---|---|---|---|---|
| 1 | Un PR introduce un ciclo nuevo y pasa a producción | No existe barrera estructural: Python tolera el círculo hasta que el reload lo revienta | Gate D1: `build()` falla nombrando los módulos del ciclo | Arranque local/CI roto ANTES del merge; test Tarjan |
| 2 | Ciclo "tolerado" revienta tras una cascada de hot-reload | Dentro de un SCC no hay orden seguro → referencias mezcladas viejas/nuevas (bug `unwrap` visto en producción) | D2 elimina los SCC existentes; D1 impide crear nuevos; residuales se recargan al final con warning visible | Warning del reloader + test discriminante de identidad |
| 3 | El gate es demasiado estricto y bloquea un ciclo deliberado/tolerable | Caso legítimo no contemplado (ej. paquete con auto-registro mutuo) | Allowlist §2.2 con caducidad obligatoria — nunca bypass silencioso | Review del ADR al tocar la allowlist |
| 4 | Se adopta dishka "porque sí" y se paga doble fuente de verdad | Presión por modernizar sin disparador cumplido | D3 fija umbral objetivo; revisión requiere justificar contra esta tabla | Este mismo ADR en review |
| 5 | Resolución dinámica falla en runtime | `resolve_service` no encuentra el servicio registrado | El composition root registra `project_service` como clase; fallback a import directo en `except` | Error explícito con next_step |

---

## 4. Consecuencias

**Positivas**
- La clase entera de bugs "ciclo descubierto tres meses después" muere en el arranque, con diagnóstico accionable.
- Los 2 ciclos restantes se cerraron con patrones YA probados en este repo (cero inventiva nueva).
- Cero dependencias nuevas; el estilo own-engine del proyecto se preserva.
- La decisión sobre dishka queda documentada CON condiciones de re-apertura — no es un "no" perpetuo.
- El gate DAG detecta ciclos en tiempo de compilación del composition root, no en runtime.

**Negativas / costos aceptados**
- ~40 líneas de validación propia que mantener (vs delegar en lib).
- Los ciclos 2º (delta↔project) requirieron mover imports a resolución dinámica via service locator en puntos específicos — trabajo de diseño genuino.
- El gate corre por cada rebuild post-reload (costo medido del grafo AST completo: ~150-200ms, dentro del presupuesto de cascada).
- La resolución dinámica introduce un acoplamiento leve al service locator en `delta_tracking`, justificado como parche estructural temporal.

**Relación con otros ADRs**: refuerza 045/046 (composition root único y con ciclo de vida explícito — ahora además autvalidado); ejecuta parte del gap catalogado en 051.

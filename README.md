# Walker

Walker es un runtime de workflow y memoria para agentes de código. Combina un
motor de workflow con cuatro memorias consultables (CPG, CKG, Delta y Trace) y
un plugin de control de vuelo para que el agente sepa en qué paso está, qué
decisiones aplican y qué le falta verificar.

## Qué incluye

- **Workflow engine**: nodos, sub-estados, gates y side effects.
- **CPG**: grafo estructural del código (ast-grep/tree-sitter), métricas,
  dead code, security patterns, DFG/CFG aproximados.
- **CKG**: decisiones de arquitectura (ADR), convenciones y hechos de sesión.
- **DeltaGraph**: sprints, specs, tareas, criterios y predicados.
- **Trace**: eventos de sesión, tool calls, verificación y cierre.
- **Plugin `walker-flight-control`**: inyecta contexto, bloquea cierres sin
  verificación verde y protege el alcance del plan.
- **CLI `walker3`**: superficie de tools por proyecto (`walker <proyecto>/<tool>`).

## Requisitos

- Python 3.11+
- Node.js 20+ (para el plugin y el harness)
- `ast-grep` (`sg`) 0.44+ en el `PATH`
- `pytest`

## Instalación

```bash
git clone https://github.com/dalarcon0118/walker.git
cd walker
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Uso rápido

```bash
# Estado del proyecto
./bin/walker3 walker/status '{}'

# Contexto consolidado del agente
./bin/walker3 walker/brief '{"session_id":"<sesión>","file":"fast_mcp/server.py"}'

# Vista humana del estado
./bin/walker3 walker/cockpit '{"session_id":"<sesión>"}'

# Suite de tests
./bin/walker3 walker/run_tests '{"task_id":"SPEC-001","session_id":"<sesión>"}'
```

## Estructura

```
fast_mcp/
  features/            # bounded contexts (delta, cognitive, code, trace, workflow, tasks)
  phases/              # superficie MCP (prepare, memories, tasks)
  memories/            # handlers de memoria (brief, cockpit, search, code analysis)
  shared_kernel/       # paths, storage, project_support
  composition.py       # composition root
  server.py            # build_server()
agent-plugins/
  walker-flight-control/  # plugin de inyección y gates
bin/walker3            # CLI
data/workflow/         # nodos y edges del workflow
tests/                 # BDD + integración
docs/                  # ADRs, specs y arquitectura
scripts/               # métricas, ingest, harness
```

## Tests

```bash
python3 -m pytest -q
```

Harness del plugin:

```bash
cd agent-plugins/walker-flight-control
node --test *.test.mjs
```

## Principios

- **Domain-first**: el dominio es SSOT; la infraestructura se adapta.
- **Fail-fast**: sin fallbacks silenciosos ni degradación implícita.
- **Result[T, E]**: las operaciones fallibles retornan `Result`.
- **Toda mutación pasa por DeltaGraph** (INV-004).
- **El cierre exige evidencia**: sin verificación verde no hay cierre.

## Licencia

MIT. Ver [LICENSE](LICENSE).

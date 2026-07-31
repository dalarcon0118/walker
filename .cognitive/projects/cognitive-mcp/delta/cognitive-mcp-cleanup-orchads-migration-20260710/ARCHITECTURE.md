# Architecture — 4 BC + Instruction Module

## Resumen

Arquitectura de 4 Bounded Contexts + shared/infra + Instruction Module:

| BC | Propósito | SSOT |
|----|-----------|------|
| **agent/** | Front MCP + estado cognitivo | Identidad de sesión, artefactos cognitivos (facts, decisions, rules), trust, contexto (workspace, project_preferences), DK |
| **workflow/** | Motor de DAG genérico (n8n-like) | Grafo de pasos declarativos (WorkflowDefinition), estado de ejecución (WorkflowState), gates |
| **tools/** | Catálogo de capacidades + Instruction Module | Registro de tools (decorator `@tool`), schemas input/output, mapeo task_type→tool, instrucciones para el LLM |
| **scrum/** | Gestión de proyecto | Sprints, tareas, deltas, goals, task decomposition |
| **shared/** | Solo frameworks arquitectónicos | CQRS bus, DI, DB config. NUNCA lógica de dominio ni infraestructura de BC |

---

## SSOT por cada feature actual

| Feature actual | Responsabilidades | SSOT | Razón |
|---------------|-------------------|------|-------|
| **session/** identidad | Session.id, topic, agent_id, started_at | **agent/session/identity** | El agente "sabe" quién es |
| **session/** DK | DunningKrugerGap, Estimate, KnowledgeGain | **agent/session/dk** | Autoevaluación cognitiva |
| **session/** snapshots | StateSnapshot, MissingDatum, ResolvedDatum | **agent/cognitive/snapshots** | Estado del conocimiento |
| **session/** fases | Phase machine (IDLE→CLOSED) | **workflow/definitions/cognitive_phase.yaml** | Es un DAG definition, no código |
| **session/** propuesta siguiente paso | `next` steps válidos | **workflow** (implícito en el grafo) | El DAG define las transiciones |
| **session/** propuesta de tarea | Plan de implementación (Proposal) | **scrum/task/proposal** | Es planificación del sprint |
| **session/** task decomposition | TaskContext, Hypothesis, Evidence | **scrum/task/decomposition** | Seguimiento científico de tareas |
| **decisions/** schemas | FactType, FactSchema, DecisionSchema | **tools/instructions/schemas** | Contrato de la tool |
| **decisions/** tools | register_fact, create_decision | **tools/fact/** | Herramientas MCP |
| **decisions/** datos | Facts, decisions registrados | **agent/cognitive/** | Memoria del agente |
| **decisions/** validación | SchemaValidator | **tools/instructions/schemas** | Parte del contrato |
| **auto_facts/** análisis | DiffAnalysis, pattern matching | **tools/auto_facts/** | Tool como cualquier otra |
| **auto_facts/** schemas | DiffFact, DiffChangeKind | **tools/instructions/schemas** | Contrato de la tool |
| **auto_facts/** datos | Facts extraídos | **agent/cognitive/** | Misma memoria que facts manuales |
| **cpg/** tool + dominio | Code analysis (symbols, DFG, CFG) | **tools/cpg/** | Tool de análisis de código |
| **cpg/** schemas | Input/output schemas | **tools/instructions/schemas** | Contrato de la tool |
| **engine/api/** MCP tools | health, onboarding, skill | **agent/api/** | Puerta de entrada del agente |
| **trust/** score | Cálculo de confianza | **agent/trust/** | Autoevaluación |
| **trust/** gate | ¿Puedo escribir? → condición DAG | **workflow/gates/trust_gate** | Gate del workflow |
| **workspace/** | Proyectos, foco | **agent/context/workspace** | Contexto del agente |
| **project_preferences/** | Stack, convenciones | **agent/context/preferences** | Perfil del proyecto |
| **navigation/** | Patrones cognitivos, historial | **agent/navigation/** | Meta-cognición del agente |
| **write_enforcement/** | Gate de escritura | **workflow/gates/write_enforcement** | Condición de transición |
| **cqrs/** | Bus genérico | **shared/cqrs** | Patrón de infraestructura |
| **git/** | Operaciones git del sprint | **scrum/infrastructure/git** | Infraestructura de scrum |
| **memora/** | Memoria semántica FTS5 | **agent/infrastructure/memora** | Infraestructura del agente |
| **tracing/** | Trazas de ejecución | **workflow/infrastructure/tracing** | Infraestructura del workflow |
| **kv/** | Persistencia clave-valor | **Cada BC** tiene su capa | No compartido |
| **acl/ports** | Interfaces arquitectónicas | **shared/ports** | Contratos genéricos |

---

## Principios de diseño

1. **Cada BC tiene su propia infraestructura.** Solo lo arquitectónico (ORM, ODM, CQRS bus) va en shared.
2. **El workflow es un motor DAG genérico.** No sabe de cognitivo, facts, ni código. Solo sabe de pasos con `tool`, `requires`, `produces`, `next`.
3. **El Instruction Module es el SSOT de schemas tools.** Define qué tools existen, qué task types resuelven, cómo se usan, y cómo se guardan los datos.
4. **Los artefactos cognitivos (decisions, facts, rules) se dividen:** la tool que los registra está en `tools/`, los datos registrados están en `agent/cognitive/`, el schema está en `tools/instructions/schemas/`.
5. **Session NO es un módulo unificado.** Sus piezas se distribuyen: identidad→agent, fases→workflow (YAML), tareas→scrum.

---

## Instruction Module: el corazón de la arquitectura

```
tools/instructions/
├── schemas/              ← SSOT de la ESTRUCTURA de cada artifacto
│   ├── fact.py           ← { type, domain, content, project_id, ... }
│   ├── decision.py       ← { decision, rationale, alternatives, ... }
│   ├── code_lookup.py    ← { query, symbol_name, file_path, ... }
│   └── ...
│
├── task_tool_map.py      ← SSOT del MAPEO task_type → tool(s)
│   lookup("analyze_code")   → [code_lookup, code_analyze]
│   lookup("record_decision") → [register_fact]
│   lookup("plan_sprint")    → [delta_plan, create_sprint]
│
├── tool_registry.py      ← SSOT del CATÁLOGO de tools
│   @tool(                   ← Decorador de auto-registro
│       name="register_fact",
│       task_types=["record_decision", "record_constraint"],
│       schema=FactSchema,
│       instruction="Usa esta tool para registrar un hecho...",
│       storage="agent/cognitive/facts"
│   )
│
└── agent_instructions.py ← Genera texto para el LLM
    def instruction_for(task_type: str) -> Instruction:
        tool = tool_registry.lookup(task_type)
        return Instruction(
            tool=tool.name,
            schema=tool.schema.describe(),
            usage=tool.instruction,
            storage=tool.storage,
        )
```

---

## Flujo de ejecución

```
LLM → agent/api → workflow/engine → tools/instructions → workflow → prompt al LLM
                      │                      │
                      │ consulta             │ responde:
                      │ "¿qué tool           │ tool, schema,
                      │  para este step?"    │ instrucción
                      ▼                      ▼
                 WorkflowState          Instruction
                 (current_step)         (tool + schema + usage)
```

---

## Cross-BC dependencies

```
agent ──(trust query)──▶ workflow/gates/trust_gate
agent ◀──(writes)──────── workflow/gates/write_enforcement
agent ◀──(calls tools)── tools/
agent ──(stores facts)──▶ agent/cognitive/
workflow ──(queries)────▶ tools/instructions/
workflow ──(persists)───▶ workflow/infrastructure/kv/
scrum ──(git ops)───────▶ scrum/infrastructure/git/
todos ──(cqrs)──────────▶ shared/cqrs/
```

Análisis: reducir mantenimiento y eliminar duplicidad
Mapa de duplicidad (5 categorías, con costo de mantenimiento)
D1. Composition roots paralelos (3 → 1)

Root	Ubicación	Problema
bootstrap()	bootstrap.py:124-175	tasks+side_effects+modules; separated=True ya devuelve domain/system/SE — nadie lo consume
bootstrap_modules()	bootstrap.py:204-222	ModuleRegistry + servicios DI cognitive_searcher/cognitive_graph/path_resolver
tool_catalog.list_tools/get_tool	tool_catalog.py:65-70, 128-130	InMemoryTaskRegistry(tracer=None) efímero por llamada — sin tracer, sin side effects → ya difiere del canónico
Composición inline	handlers.py:614-619, 701-705	SubmitDataUseCase(WorkflowRepository(), ...) a mano por handler
→ Costo: si cambia el registro de tasks, hay que tocar N puntos; el efímero sin tracer desincroniza (clase de bug real de tool_catalog).

D2. Dispatch bifurcado CLI vs MCP (2 → 1 Dispatcher)

MCP: mcp_adapter._call_tool (384-425) — bifurca ModuleRegistry route vs fallback task.
CLI: cli_interface.main (326-369) → handle_task (735-833) que hace bootstrap() + registry.execute() + reglas de visibilidad internal re-implementadas (766-784).
→ Costo: cada entrada ha resuelto su propia traducción de payload (_payload vs flags vs query→text); corregir un bug de contrato hay que hacerlo en 2 lugares.

D3. Tasks duplican libs en vez de servicios de módulos

kg.py:201-281 usa CognitiveGraph directo; kg.py:287-294 y capture_task.py:309-312 instancian CodeKnowledgeGraph directo.
Corrección importante: CognitiveGraphSearcher (cognitive.py:30-50) ya expone search/search_many/schema/list_indexes — el failure_scenario 3 que documenté era inexacto en la API; el gap real es que la DI no se usa. La migración es directa (mismo contrato), no requiere extender el searcher (solo kg_search_superseded irá por la factory CKG).
→ Costo: la lógica de acceso a datos vive en las tasks en vez de en los servicios → duplicación de parsing (_payload, alias query→text, _kg_search_payload en kg.py:232-281), y handlers/tasks no comparten contrato (handlers sí usa MemoryQueryService en 263-269 — inconsistencia interna).

D4. Visibilidad de tasks con doble fuente (2 → 1)

system_task_registry.py:19-26 _PUBLIC_TASKS hardcodeado + :50-60 _is_public_task consulta metadata.scope.
bootstrap(separated=True) (165-171) ya particiona por scope (internal/domain → domain_registry; resto → system_registry).
→ Costo: añadir/renombrar una task pública exige tocar el module register() + el frozenset.

D5. Providers declarados vs registrados (ya sintomático)

bootstrap_providers (61-105) declara task/state/diff/test_results; providers.py:192-195 solo registra diff/test_results; _discover_and_register traga excepciones (37-38).
→ Costo: degradación silenciosa del ContextComposer (provider 'task' not registered — skipping observado).

Arquitectura objetivo
text
MCP ─┐                                  ┌─ CLI
     ├──► Dispatcher único ◄────────────┤
REST ─┘   (resuelve tool → route →      └─ REST
           flags_builder → handler;
           fallback system_registry.execute)
                    │
                    ▼
   Composition root único: bootstrap(separated=True) + get_module_service()
                    │
        ┌───────────┼───────────────┐
        ▼           ▼               ▼
 domain_registry  system_registry  se_engine
 (tasks internas)  (tasks públicas)  (side effects)
        │           │
        ▼           ▼
  servicios de módulos      tasks = adapters delgados
  (spring_service — patrón   (validan TaskInput, traducen
   ya modelo en delta_tracking/__init__.py; CpgQueryService;   TaskResult; 0 libs)
   factory CodeKnowledgeGraph; DI cognitive_searcher)
Reglas (una fuente por decisión):

Todo cambio de catálogo = metadata del register() (1 lugar)
Todo acceso a datos = servicio de módulo o DI (0 libs en tasks)
Toda entrada usa el mismo Dispatcher (0 bifurcación)
Toda visibilidad = metadata.scope (0 hardcode)
Los módulos exponen fachada raíz + composition root desde el __init__ (el patrón delta_tracking.spring_service es el modelo a replicar en cognitive_knowledge/code_analysis)
Reducción de mantenimiento: herramientas nuevas = 1 register + 1 registro MCP; puntos de cambio de catálogo N→1; registries que desincronizan 3→1; imports de libs en tasks → 0.
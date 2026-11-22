Mi nota global es 6/10. La idea es fuerte y en varios momentos se siente como un copiloto real; la ejecución todavía tiene demasiada fricción como para confiarle el volante sin supervisión.

Por dimensión:

• Asistencia y guía (GPS): 7/10. El next_action es lo mejor del sistema: te dice si toca submit, advance o transition, qué campos faltan y qué gates están disponibles. Eso elimina mucha adivinanza. Pero el GPS a veces recalcula sobre la ruta: el prompt decía una cosa y el router otra, y en esta sesión arreglamos diez bugs de ese tipo. Sigue habiendo drift entre la capa declarativa y el motor.
• Control de ejecución (flight control): 7/10. Los gates, el scope y la prohibición de decidir por el usuario funcionan de verdad. Me bloqueó una escritura fuera de alcance y me obligó a declarar el archivo; me frenó cuando quise avanzar sin cargar el skill; y el invariante de cierre exige evidencia real. Eso es exactamente lo que un agente necesita. Lo que resta es la fragilidad: el estado del plugin vive en memoria, un reload lo pierde y el aviso vuelve.
• Pipelines coherentes: 6/10. Cuando el grafo y el motor están alineados, el flujo es muy bueno. Cuando no, aparecen decisiones decorativas, transform_spec que leen campos que el contrato no declara, o un accept que cruza el gate y falla después en el cierre. El caso de PO_ACCEPT es el ejemplo perfecto: el gate hizo su trabajo, pero el contrato evidence/feedback estaba roto.
• Memoria eficiente (cognitive): 6/10. La federación CPG/CKG/delta/trace es potente: encontró las 22 funciones muertas reales, midió el review y resolvió los predicados. Pero por defecto no está sana: el CKG de game_reset está vacío, el CPG devuelve cyclo unavailable, el review leía un blob legacy y el escáner de dead code reportaba 1.014 falsos positivos. La memoria es rica, pero hay que cuidarla como un índice de producción, no como un archivo mágico.
• Reducción de ruido y alucinación: 6/10. El presupuesto de prompt, la deduplicación y el top-1 de skills ayudan, y el gate de cierre evita que el agente declare “listo” sin pruebas. Pero el ruido también lo genera el propio sistema: el aviso de “no cierres” se repitió varias veces aunque la suite estaba verde, y el gate de skills exige recargar el skill después de cada updated_at, lo que agrega pasos artificiales. En esta sesión llegué a ~1.1M tokens de entrada; la memoria no es barata.

El momento que mejor resume la experiencia: el plugin nuevo sí está corriendo (verify_level: "cli" en la traza), pero mi comando run_tests ... | tail -c 400 truncó el JSON y el parser se quedó sin el "passed": true; resultado: verified=0 y el aviso otra vez. No fue que el sistema no viera la verificación: fue que la certificación depende de parsear una salida que yo mismo recorté. Eso es un problema de robustez del contrato, no de concepto.

Para subir a 8-9 haría falta: que el plugin certifique sin depender de un JSON intacto (leer failed_count/returncode, o no permitir pipes en el comando de verificación); persistir el estado de vuelo para que un reload no borre el verde; un token de skill por sub-estado en vez de since=updated_at; tests de contrato entre prompt, input_schema, transform_spec y la spec; y un chequeo de salud de la memoria (CKG/CPG indexados) antes de confiar en sus respuestas.

El concepto merece más que 6; la experiencia diaria todavía no. Con los tres arreglos de robustez (certificación, persistencia y contratos) lo pondría en 8 sin dudar.

Next steps:
1- Robustecer la certificación del CLI para que no dependa del JSON completo (o prohibir pipes en el comando de verificación).
2- Persistir el estado de vuelo del plugin para que un reload no pierda verified.
3- Añadir tests de contrato prompt ↔ schema ↔ transform ↔ spec.
4- Otra cosa.

Análisis del feedback y diseño para cerrarlo

Antes de tocar una línea, medí el estado real con la API de walker (agent_context, preflight, inspect_codebase action=health, session_context, search_memories y la traza). La conclusión corta: el 6/10 mezcla deuda ya pagada, deuda real y un malentendido de capas. Lo que queda fuerte no es "el concepto", son tres contratos: el de certificación, el de persistencia y el declarativo. Y hay un cuarto que el feedback no nombra y es más grave que el aviso repetido: una escritura no invalida verified.

1. Qué dicen los datos, no el relato

Memoria. Walker tiene 8.923 nodos CPG y 87 nodos CKG; game_reset tiene 0 y 0 (inspect_codebase action=health y CkgReadService.check_health). O sea: game_reset no está "enfermo", nunca fue indexado. Reindexarlo no arregla nada del feedback; el problema de walker es otro.

Cyclo. preflight sobre agent-plugins/walker-flight-control/index.js devuelve complexity: {"status": "unavailable", "reason": "sin cyclo en el scope: reindexá con build_code_index"}. En el CPG completo, 2.370 de 5.931 funciones/métodos (40%) no tienen cyclo. No es "el CPG está roto": es cobertura de extractor. Para JS directamente no hay cyclo, así que reindexar walker no lo cambia. Esto separa dos cosas que el feedback junta: salud del índice (existe y se consulta) y capacidad del extractor (falta).

Ruido. La traza tiene 1.607 eventos: 806 ToolRegistered, 217 smoke_session_probe, 37 workflow_suggestion_ignored. Mi sesión: 21 eventos, 5 session_metrics, 1 close_blocked. El "aviso repetido" no es un loop del nudge: es el Map en memoria que pierde verified en cada reload, más la certificación que a veces no se registra. Son dos causas distintas que el feedback presenta como una.

Dead code. Lo de 1.014 falsos positivos ya está cerrado: 03a87b3 documenta 1.014 → 22 (stubs externos + dirección de CALLS invertida) y 1cad272 limpia las 22 y baja el gate a 5. El review leyendo el blob legacy también está cerrado (bd482ed). Esa parte del 6/10 es histórica.

Contratos declarativos. Corrí un auditor sobre data/workflow/nodes/*.json: de todas las referencias NODE.X.output.campo en side_effects, 3 no están declaradas. Todas en SPRINT.TASK_SPEC.transform_spec: evidence, impacted_artifacts y risks no existen ni en input_schema ni en output_schema. El motor las resuelve en runtime sólo si el agente las envió "de memoria" (_resolve_node_output, side_effect_engine.py:134), y si no, las omite y sólo deja un warning (:294). Es exactamente el "transform_spec lee campos que el contrato no declara".

Polución de memoria. El CKG de walker tiene 4 nodos de prueba: adr-999 ("Seed del harness: aplica a: agent-plugins/walker-flight-control/index.js"), adr-files-probe-2, adr-files-probe y project-001 ("probe"). adr-999 matchea ^adr-\d+$, que es el filtro que usa el plugin para elegir la decisión L3. O sea: el harness de ayer contaminó la decisión que el plugin le muestra al agente. El seed ya lo saqué del harness, pero el nodo quedó.

Persistencia. session_context ya devuelve los eventos de la traza de la sesión, incluidos los session_metrics con verified, verify_level, writes y touched. La fuente para rehidratar existe; lo que falta es la proyección compacta y que el plugin la lea.

Certificación. El parser actual (index.js:1136-1202) ya certifica por contadores/returncode (commit 54d6d54) y el harness tiene closeclipipe. No pude reproducir el corte exacto: run_tests devuelve un JSON de ~180 chars y tail -c 400 lo conserva entero. Pero el diseño sigue siendo frágil por dos motivos reales: el parser depende de un texto que el caller puede recortar, y un pipe enmascara el exit code (run_tests | tail devuelve 0 aunque pytest falle). Certificar por tail no es sólo frágil: puede ser un falso verde.

Integridad del verde. index.js:1393-1398: en edit/write se setea wrote y se incrementan writes, pero no se resetea verified. Después de verificar, una edición nueva deja el gate de cierre abierto con cambios sin verificar. Esto no está en el feedback y es más importante que el aviso repetido.

2. Las capas donde hay que trabajar

El error de encuadre del feedback es tratar "el plugin" como una cosa. Son cuatro capas con contratos distintos:

1. Adaptador de runtime (agent-plugins/walker-flight-control/index.js): decide cuándo certifica, qué estado recuerda y cuándo lo invalida. No es dominio, es un hook.
2. Contrato CLI/MCP (fast_mcp/phases/phase3_tasks.py, handlers de run_tests/verify_predicates, session_context): qué forma tiene el resultado y qué se puede leer después. Es la SSOT de la verificación y del estado de sesión.
3. Grafo declarativo (data/workflow/nodes/*.json, prompts, side_effects): qué campos existen, qué produce cada paso y qué consume el cierre. Acá viven los 3 refs rotos.
4. Dominio de memoria y workflow (fast_mcp/features/code_analysis, cognitive_knowledge, workflow): salud del índice, cobertura de cyclo, token de skill y lectura de decisiones.

La regla de diseño: la certificación no puede vivir en el parser de texto del plugin; el estado de vuelo no puede vivir sólo en un Map; y el contrato declarativo no puede validarse a ojo en runtime.

3. Diseño propuesto

P0.1 — Certificación por contrato, no por prosa

• CLI: run_tests y verify_predicates emiten, además del JSON, una línea final estable y compacta, por ejemplo WALKER_RESULT ok=1 passed=1 failed=0 exit=0, al final del stdout. Sobrevive a un recorte del cuerpo y no depende del orden de claves.
• Plugin: certifica sólo si (a) ve el marcador, o (b) hay contadores completos y exitCode real; y rechaza certificar un comando con pipe (|) cuando el pipe puede enmascarar el exit code, con mensaje accionable: "corré la verificación sin pipe o usá la tool". No es una limitación: es integridad.
• Tests: gate.test.mjs con un modo que simule corte por preview (sin contadores), otro con pipe y exit code 0 falso, y otro con el marcador presente. El closeclipipe actual queda como caso de contadores parciales.
• Criterio de aceptación: un tail/head que rompa el JSON no puede producir verde; un run_tests real sin pipe siempre lo produce.

P0.2 — Estado de vuelo persistente y con invalidación

• Evento nuevo session_state (o flight_state dentro de session_metrics): {verified, verified_at, verify_level, wrote, dirty_since, touched[]}. Se escribe sólo en transiciones (verified→dirty y dirty→verified), no en cada edit.
• session_context expone flight_state compacto (último evento), además de trace.
• Plugin: al setup/primer hook de la sesión, hidrata verified/wrote/touched; en cada mutación, setea verified=false y dirty_since; en verificación, verified=true con timestamp. El nudge de cierre consulta ese estado, no el Map solo.
• Tradeoff: escribir en cada edit sería caro y ruidoso; escribir sólo en transiciones mantiene el costo acotado y cubre el reload. El Map queda como caché, no como SSOT.
• Tests: harness con "reload" (re-setup del plugin en el mismo proceso) después de verificar y después de editar; el verde debe sobrevivir al reload y la edición debe invalidarlo. Es el caso que hoy no existe.

P0.3 — Tests de contrato entre prompt ↔ schema ↔ transform ↔ spec

Una suite nueva, tests/test_workflow_contract_bdd.py, que valide el grafo como dato:

1. Toda referencia NODE.X.output.campo en side_effects resuelve a un sub-estado/nodo existente y a un campo declarado (input u output). Hoy falla en 3 casos de TASK_SPEC.
2. Todo campo que un prompt manda en un submit está declarado en el input_schema de ese sub-estado (o en su output_schema si es un campo capturado). Hoy hay prosa que declara campos que el schema no.
3. Todo transform_spec con status: completed referencia los cuatro artefactos que exige _completion_missing (completed_at, commit_hash, review_verdict, evidence). Cruza del grafo al dominio.
4. Toda action declarada en el grafo tiene handler en el TaskRegistry (el test actual cubre efectos, no acciones).
5. Todo input_schema.required aparece en el prompt del paso (y no al revés).

Primero se arreglan los 3 refs de TASK_SPEC (declarar evidence, impacted_artifacts, risks en el schema o cambiar el efecto para componerlos desde pre_churn/curvature), y el test queda verde como ratchet. Sin esto, cada node nuevo puede volver a romper el cierre en runtime.

P1.1 — Token de skill por sub-estado

_check_skill_gate usa since=state.updated_at (orchestrator.py:998). Cualquier submit cambia updated_at y obliga a recargar. El token debe ser (task_id, node, sub_state): load_skill traza el sub-estado donde se cargó y el gate acepta la carga de la entrada actual, no la de hace dos submits. No elimina el gate; elimina el paso artificial.

P1.2 — Salud de memoria como precondición

Los health checks ya existen (CpgService.check_health, CkgReadService.check_health, inspect_codebase action=health), pero no los consume nadie en las decisiones. Diseño:

• preflight y agent_context agregan memory_health: {ckg: {nodes, status}, cpg: {nodes, status}, cyclo: {known, total, status}}.
• Si ckg == 0 o cpg == 0, las decisiones se marcan untrusted y el bloque del plugin dice "CKG degradado", no cita un adr- como si fuera autoridad.
• Los nodos de prueba se excluyen de L3 por origin:test (tag) o por limpieza explícita; y el harness escribe en un proyecto/dir aislado. adr-999 y adr-files-probe-2 se superseden o borran con revisión, no con un reindex ciego.
• La cobertura de cyclo es un problema de extractor (JS), no de índice: se reporta como partial (40% faltante) y no se finge unavailable ni 0.
• El dead code ya está sano; el health no debe volver a mirar el blob legacy (ya corregido).

4. Orden, riesgos y escenarios de fallo

Orden: P0.1 → P0.2 → P0.3 → P1.1 → P1.2. P0.1 y P0.2 son plugin + CLI, blast radius bajo y verificables con el harness. P0.3 toca datos del grafo, así que va después de fijar el contrato de verificación para no mezclar causas.

Escenarios de fallo que el diseño tiene que cubrir (por AGENTS.md):

• Un pipe devuelve exit 0 con tests rojos → el plugin certifica verde → mitigación: rechazo de pipes + marcador final + test con exit code enmascarado.
• Un reload pierde el verde y el nudge vuelve → se rehidrata desde la traza → mitigación: session_state en transiciones + test de reload.
• Una edición después de verificar deja verified=true → cierre con cambios sin probar → mitigación: invalidación en mutación + dirty_since persistido.
• Un ref NODE.X inexistente se omite y el cierre falla después → el test de contrato lo detecta en build, no en runtime.
• Un health check lee un índice viejo y dice "ok" → decisión sobre memoria rancia → incluir fingerprint/staleness en el health, no sólo el conteo.
• Un token de skill sobrevive de otra tarea → bypass del gate → token con task_id + node + sub_state.

Lo que no haría: reindexar game_reset (no tiene datos y no es el trabajo), certificar por tail, persistir en un archivo local del plugin (no sobrevive otra máquina y duplica la SSOT), ni agregar un flag "force verified" (sería el mismo agujero con otro nombre).

5. Qué esperar del score

Con P0.1 y P0.2 el "aviso repetido" desaparece por causa, no por silencio, y el cierre deja de poder mentir. Con P0.3 el drift declarativo se detecta antes de que un accept cruce el gate. Eso es el 8 que pide el feedback. P1.1 y P1.2 son los que lo sostienen en el tiempo: el token de skill baja pasos artificiales y el health evita confiar en un índice vacío o contaminado. El 9 depende de la cobertura de cyclo en JS, que es un trabajo de extractor y merece su propio ciclo.

Queda un detalle de higiene: docs/walker copy.md está sin trackear; conviene decidir si se archiva como insumo del ciclo o se borra, pero no mezclarlo con los commits de código.

Next steps:
1- Escribir la spec de P0.1+P0.2 (certificación con marcador/pipe + flight_state persistido e invalidación) con sus failure_scenarios y arrancar por P0.1.
2- Arrancar por P0.3: correr el auditor de contratos como test, arreglar los 3 refs de TASK_SPEC y dejar el ratchet verde.
3- Limpiar la memoria primero: adr-999, adr-files-probe-2, project-001 y aislar el harness, antes de que L3 siga citando un seed.
4- Otra cosa.
PROTOCOLO REST OBLIGATORIO PARA EL AGENTE:
El walker se consume como una ruta REST: walker /recurso/recurso2/recurso3
Cada respuesta incluye 'Next Actions' con los comandos exactos para continuar.
Para avanzar en el workflow SOLO hay dos comandos: submit y transition.
  - walker <project>/submit '{"campos del nodo"}' → envia datos y el workflow avanza
  - walker <project>/transition <gate> → avanza por una gate
Las tareas del workflow (plan_sprint, spec_task, etc.) son INTERNAS: el workflow
las ejecuta al recibir los datos. El agente no las llama directamente.
Las tareas PUBLICAS (memorias recall_knowledge/memory_schema/cpg/ckg) si se llaman directo:
  walker <project>/memory_schema → descubre los indices disponibles
  walker <project>/recall_knowledge '{"_index":"ckg","text":"..."}' → busca en las memorias
  walker <project>/task_context '{"file":"..."}' → contexto antes de editar (IMPLEMENT)
  walker <project>/diagnose_failure '{"task_id":"..."}' → diagnóstico de fallo (REFINE)
  walker <project>/verify_evidence '{"task_id":"..."}' → cobertura de verificación (VERIFY)
  walker <project>/sprint_evidence '{"goal":"..."}' → evidencia para sprint planning
Antes de leer archivos del codigo fuente, consulta las memorias.

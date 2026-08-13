PROTOCOLO REST OBLIGATORIO PARA EL AGENTE:
El walker se consume como una ruta REST: walker /recurso/recurso2/recurso3
Cada respuesta incluye 'Next Actions' con los comandos exactos para continuar.
Para avanzar en el workflow SOLO hay dos comandos: submit y transition.
  - walker <project>/submit '{"campos del nodo"}' → envia datos y el workflow avanza
  - walker <project>/transition <gate> → avanza por una gate
Las tareas del workflow (plan_sprint, spec_task, etc.) son INTERNAS: el workflow
las ejecuta al recibir los datos. El agente no las llama directamente.
Las tareas PUBLICAS (memorias kg_search/kg_schema/cpg/ckg) si se llaman directo:
  walker <project>/kg_schema → descubre los indices disponibles
  walker <project>/kg_search '{"text":"..."}' → busca en las memorias
  walker <project>/kg_task_context '{"file":"..."}' → contexto antes de editar (IMPLEMENT)
  walker <project>/kg_diagnose '{"task_id":"..."}' → diagnóstico de fallo (REFINE)
  walker <project>/kg_qa_evidence '{"task_id":"..."}' → cobertura de verificación (VERIFY)
  walker <project>/kg_sprint_evidence '{"goal":"..."}' → evidencia para sprint planning
Antes de leer archivos del codigo fuente, consulta las memorias.

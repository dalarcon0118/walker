# language: es
# tests/features/mcp_tools_list_equivalence.feature
#
# Feature: Equivalencia entre tools/list MCP y registry.list_tasks (ADR-043).
#
# El MCP expone tools/list; el registry (ModuleRegistry + TaskRegistry) es el
# SSOT. Este test garantiza que no haya drift entre ambos.

Característica: Equivalencia MCP tools/list vs registry
  Como agente MCP
  Quiero que tools/list sea exactamente el registry
  Para no tener tools fantasma ni tools invisibles

  @contrato @ssot
  Escenario: tools/list MCP coincide con registry.list_tasks
    Dado el catálogo completo de tools del registry
    Y la respuesta de tools/list del MCP
    Entonces ambos conjuntos de nombres son idénticos
    Y no hay tools fantasma en el MCP
    Y no hay tools invisibles del registry

  @contrato @ssot
  Escenario: cada tool en tools/list tiene inputSchema y outputSchema dict
    Dado la respuesta de tools/list del MCP
    Entonces cada tool tiene inputSchema tipo object con properties dict
    Y cada tool tiene outputSchema dict

  @contrato @ssot
  Escenario: las 6 tools REST del workflow tienen outputSchema derivado
    Dado la respuesta de tools/list del MCP
    Entonces las 6 tools REST del workflow tienen outputSchema con data.properties

  @contrato @ssot
  Escenario: las 4 tools de fase están en tools/list
    Dado la respuesta de tools/list del MCP
    Entonces kg_task_context está presente
    Y kg_diagnose está presente
    Y kg_qa_evidence está presente
    Y kg_sprint_evidence está presente

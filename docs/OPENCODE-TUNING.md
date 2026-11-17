# Ajustes de OpenCode medidos

Lo que Claude Code hace y OpenCode ya soporta: la diferencia es configuración,
no capacidad. Esto es lo aplicado y lo que se descartó por no existir.

## Aplicado (`~/.config/opencode/opencode.json`)

```jsonc
{
  "mcp": { "walker": { "environment": { "WALKER_MCP_PROFILE": "agent" } } },
  "compaction": { "buffer": "15%", "keep": { "tokens": 15000 } },
  "experimental": { "background_compaction": true, "subagent_depth": 2, "todo": true }
}
```

- **Perfil `agent`**: walker pasa de 38 tools (15.225 chars) a 16 (5.573), −63%
  de superficie. Antes estaba en `{}` → corría el perfil completo en todas las
  sesiones.
- **`buffer: "15%"`**: compacta al 85% de la ventana en vez del 95% que dejaba
  `"5%"`. El *context rot* pega antes del límite, no después.
- **`subagent_depth: 2`**: un subagente puede delegar; el default `1` lo impide.
- **`todo: true`**: habilita el tool de lista de tareas.

## Descartado porque el schema lo ignora

- **`compaction.prune: true`** no existe en 2.3.0: `opencode debug config` lo
  descarta en silencio. Se quitó para no dar una falsa sensación de configuración.

## Medido, no aplicado todavía

- **LSP**: no hay nativo en v2 (V1 sí tenía). Existe un plugin comunitario
  (`opencode-v2-lsp-support`) que restaura el tool `lsp` y los diagnósticos
  post-edición; requiere el language server instalado (`pyright-langserver`).
  Es la única capacidad real que falta frente a Claude Code.
- **Modelo por agente**: `build` corre con el modelo de su frontmatter
  (`beam-free`). Los subagentes pueden llevar otro modelo; no se configuró.
- **Subagentes propios**: no hay ninguno. Medimos 40-57 archivos abiertos por
  sesión fuera del alcance, que es exactamente lo que un subagente aísla.

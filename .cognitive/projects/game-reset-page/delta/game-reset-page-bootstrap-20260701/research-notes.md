# Research Notes R1-R7 — Next.js 16 Best Practices

> **Generado**: 2026-07-01
> **Trigger**: el usuario preguntó "qué guía estoy usando". Investigamos antes de F3.
> **Fuente**: `node_modules/next/dist/docs/` (docs bundled de Next 16.2.9) + skill `react-best-practices`

## 🔴 Breaking changes que afectan el plan

### 1. `params` ahora es `Promise` (async)

```ts
// ❌ Next 15 (lo que sabía)
export default function Page({ params }: { params: { id: string } }) {
  return <div>{params.id}</div>;
}

// ✅ Next 16 (correcto)
export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <div>{id}</div>;
}
```

**Aplica a**: pages dinámicas (`/audit/[id]`) y route handlers.

**Fuente**: [`route.md`](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/game-reset-page/node_modules/next/dist/docs/01-app/03-api-reference/03-file-conventions/route.md), [`page.md`](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/game-reset-page/node_modules/next/dist/docs/01-app/03-api-reference/03-file-conventions/page.md)

---

### 2. `use cache` directive reemplaza `unstable_cache` (Cache Components)

```ts
// ❌ Next 15 (lo que sabía)
import { unstable_cache } from "next/cache";

const getData = unstable_cache(
  async (id: string) => fetch(...),
  ["key"],
  { revalidate: 30 }
);

// ✅ Next 16 (Cache Components, habilitado por cacheComponents: true)
async function getData(id: string) {
  "use cache";
  return await fetch(...);
}
```

**Aplica a**: F4 (BFF-INFRASTRUCTURE) → eliminar `cache/fetch-cache.ts`, F6/F7 → usar `'use cache'` inline.

**Requiere**: `cacheComponents: true` en `next.config.ts` (ya agregado en parche retroactivo).

**Fuente**: [`use-cache.md`](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/game-reset-page/node_modules/next/dist/docs/01-app/03-api-reference/01-directives/use-cache.md), [`migrating-to-cache-components.md`](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/game-reset-page/node_modules/next/dist/docs/01-app/02-guides/migrating-to-cache-components.md)

---

### 3. `middleware.ts` → `proxy.ts` (informativo)

Next 16 renombró el file convention. Como mi plan no usa middleware/proxy, no hay impacto. Pero saberlo existe.

**Fuente**: [`proxy.md`](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/game-reset-page/node_modules/next/dist/docs/01-app/03-api-reference/03-file-conventions/proxy.md)

---

### 4. `next lint` removido (ya mitigado)

Mi plan usa Biome desde F2 → no impacta.

---

### 5. `fetch` memoization automática

Next 16 memoiza automáticamente fetch idénticos en un render tree. **No hacer cache manual de fetch si los args son idénticos** — ya está memoizado.

```ts
// ✅ Dos fetch idénticos en el mismo tree = 1 sola request
async function Header() { return <div>{await fetch('/api/me').then(r => r.json())}</div>; }
async function Main() { return <div>{await fetch('/api/me').then(r => r.json())}</div>; }
```

**Aplica a**: BFF routes y Server Components que llaman a `/api/proxy/*`.

**Fuente**: [`fetching-data.md`](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/game-reset-page/node_modules/next/dist/docs/01-app/01-getting-started/06-data-fetching.md)

---

## 🟢 Best practices confirmadas (Vercel Engineering)

Del skill `react-best-practices`:

1. **No `await` secuencial** → usar `Promise.all` para fetches paralelos
2. **No barrel imports** → `import { x } from 'lib/x'` (no `from 'lib'`)
3. **Server Components por default** → `"use client"` solo si necesita estado/efectos
4. **`<Suspense>` para streaming** → wrappear fetches lentos
5. **Dynamic imports** para componentes pesados
6. **Memoizar computaciones caras** con `useMemo` o `cache()` de React

---

## 📋 Ajustes al plan original

| Tarea | Antes | Después |
|-------|-------|---------|
| F0 | (cerrada) | + `cacheComponents: true` en `next.config.ts` (parche retroactivo aplicado) |
| F4 | `cache/fetch-cache.ts` con `unstable_cache` wrapper | Sin `fetch-cache.ts`; usar `'use cache'` inline en cada función de `application/` |
| F5 | `params: { id: string }` (sync) | `params: Promise<{ id: string }>` (async, awaited) |
| F6 | `get-landing-data.ts` con `unstable_cache` | `get-landing-data.ts` con `'use cache'` directive en la función |
| F7 | mismo | mismo |

**Tasks NO afectadas**: F1, F2, F3, F8, F9.

---

## 🎓 Fuentes consultadas

| # | Doc | Líneas leídas | Path |
|---|-----|---------------|------|
| R1 | `ai-agents.md` | 1-200 | `node_modules/next/dist/docs/01-app/02-guides/ai-agents.md` |
| R2 | `version-16.md` | 1-800 | `node_modules/next/dist/docs/01-app/02-guides/upgrading/version-16.md` |
| R3 | `backend-for-frontend.md` | 1-300 | `node_modules/next/dist/docs/01-app/02-guides/backend-for-frontend.md` |
| R4 | `migrating-to-cache-components.md` | 1-300 | `node_modules/next/dist/docs/01-app/02-guides/migrating-to-cache-components.md` |
| R5 | `route.md` (Route Handlers API) | 1-200 | `node_modules/next/dist/docs/01-app/03-api-reference/03-file-conventions/route.md` |
| R6 | `fetching-data.md` | 1-200 | `node_modules/next/dist/docs/01-app/01-getting-started/06-data-fetching.md` |
| R7 | `proxy.md` (era middleware) | 1-200 | `node_modules/next/dist/docs/01-app/03-api-reference/03-file-conventions/proxy.md` |
| R8 | `use-cache.md` (directive) | 1-200 | `node_modules/next/dist/docs/01-app/03-api-reference/01-directives/use-cache.md` |

**Skill activa**: `react-best-practices` (Vercel Engineering, 57 reglas) — cargado pero lectura selectiva aún no ejecutada (lo haré antes de F6).

---

## 🚦 Verificación del parche retroactivo

```bash
cd projects/game-reset-page
pnpm typecheck  # debe pasar
pnpm build      # debe pasar — cacheComponents: true requiere que el árbol sea "deterministic" o use <Suspense> en runtime
```

Si `pnpm build` falla, significa que cacheComponents es estricto y我们需要 wrappear runtime data con `<Suspense>`.

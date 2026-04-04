# Plan: Startup45 — AG-UI Dashboard Feature

Integrate the Second Brain Research Dashboard (text → LLM → AG-UI → rendered components) into Startup45 Next.js. Components become editable Tiptap custom blocks.

---

## Architecture

```
Phase 1 — Generation:
  Startup45 (client component)  →  FastAPI (this repo, deployed)
       ↓ HTTP POST + SSE stream        ↓ LLM pipeline
  Receive A2UI JSON specs       ←  Stream component specs

Phase 2 — Insertion into Tiptap:
  A2UI specs → converted to a2uiBlock Tiptap nodes → stored as editor content
  (AG-UI disconnected at this point — blocks are standalone)

Phase 3 — Display:
  Editor (read mode)  → renders A2UI React components from block attrs
  Editor (edit mode)  → shows editable form fields for each prop
  Published pages     → same A2UI React components, read-only
```

---

## How A2UI Components Become Tiptap Blocks

Follows the same pattern as the existing `templateCard` block in Startup45:

### Block Attributes
```typescript
// a2uiBlock Tiptap node attrs
{
  componentType: string;           // e.g., "StatCard", "HeadlineCard"
  props: Record<string, any>;      // the A2UI component props (value, label, trend, etc.)
  widthHint: 'full' | 'half' | 'third';  // column layout hint
}
```

### Read Mode
Renders the actual A2UI React component using the catalog — same visual as the AG-UI stream preview and published pages.

### Edit Mode
On click, shows editable form fields derived from the component's known prop schema (each A2UI component type has a known set of fields — label, value, items, etc.). Same pattern as `templateCard` edit mode with Shadcn form components.

### Storage
Stored as Tiptap ProseMirror nodes with JSON attrs — identical to how `templateCard` stores `metaValues`. Serializable to text for search indexing via a per-component-type serializer.

---

## Column Layouts for Tiptap

**Three column configurations to support in the editor:**

| Layout | Used by | Tiptap representation |
|--------|---------|----------------------|
| **1-column (full)** | TLDR, CodeBlock, DataTable, Section, StepCard, ComparisonTable | Single `a2uiBlock` node, full editor width |
| **2-column (half)** | KeyTakeaways, CalloutCard, QuoteCard, HeadlineCard, VsCard | Two `a2uiBlock` nodes inside a `columnLayout` node with `columns: 2` |
| **3-column (third)** | StatCard, LinkCard, ToolCard, ProfileCard, VideoCard | Three `a2uiBlock` nodes inside a `columnLayout` node with `columns: 3` |

**Skip 4-column (quarter)** — only used for Badge/Tag, too narrow for editable blocks. Render those inline or as a tag row instead.

**Implementation:** Use the existing `column-extension.ts` infrastructure already in Startup45 to create column containers. When the user inserts AG-UI results, auto-group components by their `widthHint` into the matching column layout.

---

## Tasks

### 1. Deploy FastAPI as a standalone service
- Deploy `agent/` to Railway/Render/Fly
- Env var: `OPENROUTER_API_KEY`
- Verify `/health` and `/info` endpoints respond
- Note the deployed URL for `NEXT_PUBLIC_DASHBOARD_API_URL`

### 2. Copy A2UI frontend pieces into Startup45
```
frontend/src/components/A2UI/**       → components/a2ui/
frontend/src/components/A2UIRenderer.tsx
frontend/src/lib/a2ui-catalog.tsx
frontend/src/lib/layout-engine.ts
frontend/src/hooks/useDashboardAgent.ts
```

### 3. Install dependencies in Startup45
```bash
npm install @copilotkit/react-core @ag-ui/client framer-motion
```
(lucide-react, clsx, tailwind-merge, shadcn components likely already present)

### 4. Wire up the AG-UI streaming client component
- Create `components/a2ui/ResearchDashboard.tsx` ('use client')
- `CopilotKitProvider` + `HttpAgent` pointing at deployed FastAPI
- `useDashboardAgent` for state management and streaming
- Preview panel: renders incoming components with `A2UIRenderer`

### 5. Create `a2uiBlock` Tiptap extension
Follow the `templateCard` pattern:
- **Extension:** `a2ui-block-extension.ts` — `Node.create()` with attrs: `componentType`, `props`, `widthHint`
- **Component:** `a2ui-block-component.tsx` — read mode renders via catalog, edit mode shows form fields
- **Prop schemas:** per-component-type field definitions (what fields each type has, their types, options)
- **Register** in `extensions-registry.ts` and `command-registry.ts`

### 6. Create column layout integration + insertion grouping algorithm

Use existing `column-extension.ts` to wrap `a2uiBlock` nodes in column containers.

**Default state:** Every `a2uiBlock` is full-width. No column wrapper needed. This is the natural Tiptap block state and always looks correct.

**Column wrapping is an optional enhancement** — only applied when hints produce an exact clean row. No remainders, no empty slots, no ambiguity.

**Three supported layouts:**

| Layout | When applied | Tiptap nodes |
|--------|-------------|-------------|
| **Full-width** | Default for everything | Standalone `a2uiBlock` |
| **2-column** | Exactly 2 consecutive `half`-hint components | `columnLayout(columns: 2)` wrapping 2 `a2uiBlock` nodes |
| **3-column** | Exactly 3 consecutive `third`-hint components | `columnLayout(columns: 3)` wrapping 3 `a2uiBlock` nodes |

**No 4-column.** Badge/Tag components are tiny inline elements, not structured editable blocks. Handle them as a single `TagCloud` block or inline decorations.

**Insertion grouping algorithm — runs when user inserts AG-UI results into Tiptap:**

**Rules:**
1. Look up `widthHint` per component (explicit `width_hint` from props, or default from `TYPE_DEFAULT_WIDTHS`). No hint = `full`.
2. Walk the array. Accumulate consecutive same-hint components into a "run."
3. On hint change (or array end), flush the run:
   - `full` → each component standalone.
   - `half` → consume in exact pairs → `columnLayout(columns: 2)`. Remainder → standalone.
   - `third` → consume in exact triples → `columnLayout(columns: 3)`. Remainder → standalone.
4. Long runs produce multiple stacked rows (e.g., 6 thirds → two 3-col rows).

**Examples:**
```
3×third, 2×half, 1×full         → 3-col row, 2-col row, standalone
7×third                          → 3-col row, 3-col row, standalone (remainder 1)
2×third, 1×full, 2×half         → 2× standalone, standalone, 2-col row
no hints at all                  → all standalone (default)
```

Column wrappers only appear on exact matches. Remainders are always full-width standalone. User can rearrange after insertion.

### 7. Create documentation hub — `Home-AG-UI.md`
- **Location:** Root of Startup45 repo
- **Covers:**
  - AG-UI streaming protocol (SSE events → DashboardState)
  - How `useDashboardAgent` wraps the connection
  - How `A2UIRenderer` + `a2ui-catalog` resolve JSON → React components
  - Full component type list with prop schemas for each
  - Column layout mapping (which types → which layout)
  - How `a2uiBlock` Tiptap extension works (attrs, read/edit modes, storage)
  - Conversion flow: AG-UI spec → Tiptap block insertion
  - Link to `/showcase-components` on this repo for visual reference

### 8. Validate end-to-end
- Text input → FastAPI streams specs → preview renders → insert into Tiptap → edit fields → save → published page renders correctly
- Confirm 1-col, 2-col, 3-col layouts in editor
- Confirm component rendering matches showcase

---

## Layout System Reference

### Width Hints → Column Mapping

| Width Hint | Columns | Default for these component types |
|------------|---------|----------------------------------|
| `full` | 1-col | TLDR, ExecutiveSummary, CodeBlock, DataTable, StepCard, CommandCard, ComparisonTable, FeatureMatrix, PricingTable, Section, TimelineEvent, BulletPoint, TableOfContents |
| `half` | 2-col | KeyTakeaways, CalloutCard, QuoteCard, ExpertTip, HeadlineCard, VsCard, RankedItem, ProConItem, ChecklistItem |
| `third` | 3-col | StatCard, LinkCard, ToolCard, BookCard, RepoCard, VideoCard, ImageCard, ProfileCard, CompanyCard, TrendIndicator, MetricRow |

### Semantic Zones (for AG-UI streaming preview, not for Tiptap)

| Zone | Purpose | Render Order |
|------|---------|-------------|
| `hero` | Prominent top content | 1st |
| `metrics` | Stats and KPIs | 2nd |
| `insights` | Key findings | 3rd |
| `content` | Detailed content | 4th |
| `media` | Videos, images | 5th |
| `resources` | Links, tools | 6th |
| `tags` | Labels, badges | 7th |

Zones apply during the AG-UI streaming preview. Once inserted into Tiptap, the zone concept is replaced by the user's chosen document order.

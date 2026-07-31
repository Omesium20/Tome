# Frontend Conventions

How the Next.js frontend (`frontend/`) is organized and the conventions that aren't obvious from reading any single file. `docs/architecture.md` covers the system; this covers frontend-specific decisions. Update this file when a convention here changes.

## Mock backend layer

The FastAPI backend routes are stubs, so the frontend runs against mocks that mirror the future API:

- **`src/lib/api.ts` is the only gateway to the backend.** Every function has its real `fetch` implementation, but routes through `src/lib/mock/` unless `NEXT_PUBLIC_USE_MOCKS=false`. Components never import mock modules directly — when the backend is real, flipping that flag is the whole migration. Add new endpoints the same way: real implementation + mock behind the flag.
- **`src/lib/mock/cards.ts`** — generated card pool of ~78 real cards (real Scryfall data + art URLs), the stand-in for the backend Card table. Regenerate rather than hand-edit (it was produced by a one-off script hitting Scryfall's `/cards/collection` endpoint; Scryfall requires a `User-Agent` header).
- **`src/lib/mock/metadata.ts`** — stands in for `CardMetadata.roles` from `docs/data-model.md` until the Knowledge Pipeline exists. The deck builder groups its columns by primary role. Role names are open-ended: anything not in `ROLE_ORDER` still renders, sorted after known roles, with Land always last.
- **`src/lib/mock/generate.ts`** — mock of the Deck Generation Pipeline; steps through the real pipeline's phases (`analyzing → retrieving → constructing → validating`, the `GenerationPhase` type) so the loading UI maps 1:1 onto the real backend later.
- **Mock persistence uses localStorage** so state survives refresh like a real backend would: collection under `tome.mock.collection.v1`, the working deck under `tome.deck.v1`. Mock calls add small artificial latency so loading states are exercised for real.

## Shared filter logic and UI (do not re-duplicate)

- **`src/lib/filter-cards.ts`** (`CollectionFilters`, `DEFAULT_FILTERS`, `applyFilters`) is the single source of truth for collection filtering/sorting. It is consumed by both the `/collection` page and the deck builder's `CollectionPanel`. This logic used to be duplicated and was deliberately unified — keep it that way.
- **`src/components/collection/CollectionFilterControls.tsx`** is the one component rendering the search/color/type/sort controls, reused by `CollectionToolbar` (full collection page) and `CollectionPanel` (condensed deck-builder sidebar). If a third surface needs filters, extend this component — don't fork it.

## Drag-and-drop contract

Card drag-and-drop uses **native HTML5 DnD — no library**. The contract is a custom MIME type:

```ts
export const CARD_DRAG_TYPE = "application/tome-card-id"; // CollectionPanel.tsx
```

Drag sources set the card id under that type (plus `text/plain` with the card name); drop targets check `dataTransfer.types.includes(CARD_DRAG_TYPE)` before accepting. Currently: `CollectionPanel` tiles are sources, the deck board in `app/deck-builder/page.tsx` is the target. New drop targets (e.g. dropping onto a specific role column, or the commander slot) should extend this contract — don't pull in dnd-kit for a one-off.

## Design system

- **Tailwind** with semantic tokens in `tailwind.config.ts`. Dark-only, "ancient archive meets modern AI research lab": `bg` warm charcoal, `panel` espresso, `ink` parchment text, `ink-muted`, `line` bronze-gray borders. Accents are role-scoped and used sparingly: `action` royal blue (primary actions), `ai` muted violet (AI-generated content), `gold` (commander highlights), `success` emerald, `warn` amber. Card art supplies the color; the UI stays understated — no textures, ornaments, or glow. Inter via `next/font`.
- Icons are inline SVG (never emoji). Interactive elements get `cursor-pointer`, visible `focus-visible` outlines, and 150–300ms transitions; `prefers-reduced-motion` is honored globally in `globals.css`.
- Modals use native `<dialog>` (`showModal()`) for free focus trapping, Esc, and backdrop handling.

## Gotchas

- **Card images are served `unoptimized`** (`next.config.js`): Scryfall rejects requests without a `User-Agent`, which breaks the Next image-optimizer proxy (Node fetch sends none). Browsers load Scryfall's CDN directly without issue.
- **Never run `npm run build` while `npm run dev` is running** — both write `.next/` and the dev server's chunks 404 afterward (pages render but never hydrate). Type-check with `npx tsc --noEmit` instead; restart the dev server if it happens.

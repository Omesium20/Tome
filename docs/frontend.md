# Frontend Conventions

How the Vite + React frontend (`frontend/`) is organized and the conventions that aren't obvious from reading any single file. `docs/architecture.md` covers the system; this covers frontend-specific decisions. Update this file when a convention here changes.

## Routing and app shell

A client-only SPA — there is no server component, and no page needs one (the backend is a separate FastAPI service reached only through `src/lib/api.ts`).

- **`index.html`** is the entry document and the only place page metadata lives (`<title>`, description, favicon). There's no per-route metadata API; if a route ever needs its own title, set it in an effect rather than reaching for a helmet library.
- **`src/main.tsx`** mounts the app inside `<BrowserRouter>` and imports `globals.css` plus the self-hosted Inter font.
- **`src/App.tsx`** holds the persistent chrome (sidebar + `SideNav`) and the route table. Routes are declared explicitly here — adding a file under `src/pages/` does **not** create a route on its own.
- **`src/pages/`** holds one named-export component per route: `Home`, `Collection`, `DeckBuilder`, `Decks`. Named, not default, so the route table imports read clearly.
- **`SideNav`** uses `NavLink`'s `isActive` for highlighting; its default prefix matching is what drives the active state (`aria-current` comes free with it).

**Deep links depend on a server-side fallback.** React Router owns `/collection`, `/deck-builder`, and `/decks` client-side, so any host serving the build must rewrite unknown paths to `index.html`. The dev server and `vite preview` do this automatically; in Docker it's the `try_files` directive in `frontend/nginx.conf`. A host without that rule 404s on refresh.

## Environment variables

Only `src/lib/api.ts` reads them, and only two exist: `VITE_API_BASE_URL` and `VITE_USE_MOCKS` (documented in `frontend/.env.example`).

Vite exposes **only** `VITE_`-prefixed vars to client code, via `import.meta.env`, and **inlines them at build time** — they are not read at container startup. That's why `frontend/Dockerfile` takes them as `ARG`/`ENV` before `npm run build` and `Dockercompose.yaml` passes them under `build.args` rather than `environment:`. Setting them as runtime env vars silently does nothing. A useful consequence: with `VITE_USE_MOCKS=false` the mock modules are dead code and get tree-shaken out of the bundle entirely.

## Mock backend layer

The FastAPI backend routes are stubs, so the frontend runs against mocks that mirror the future API:

- **`src/lib/api.ts` is the only gateway to the backend.** Every function has its real `fetch` implementation, but routes through `src/lib/mock/` unless `VITE_USE_MOCKS=false`. Components never import mock modules directly — when the backend is real, flipping that flag is the whole migration. Add new endpoints the same way: real implementation + mock behind the flag.
- **`src/lib/mock/cards.ts`** — generated card pool of ~78 real cards (real Scryfall data + art URLs), the stand-in for the backend Card table. Regenerate rather than hand-edit (it was produced by a one-off script hitting Scryfall's `/cards/collection` endpoint; Scryfall requires a `User-Agent` header).
- **`src/lib/mock/metadata.ts`** — stands in for `CardMetadata.roles` from `docs/data-model.md` until the Knowledge Pipeline exists. The deck builder groups its columns by primary role. Role names are open-ended: anything not in `ROLE_ORDER` still renders, sorted after known roles, with Land always last.
- **`src/lib/mock/generate.ts`** — mock of the Deck Generation Pipeline; steps through the real pipeline's phases (`analyzing → retrieving → constructing → validating`, the `GenerationPhase` type) so the loading UI maps 1:1 onto the real backend later.
- **`src/lib/mock/decks.ts`** — stands in for the saved-deck endpoints (`GET/POST/PUT/DELETE /api/decks`). Enforces the `MAX_DECKS` cap (100, defined in `src/lib/types.ts`) the way the real backend will: `saveDeck` rejects a create past the cap.
- **Mock persistence uses localStorage** so state survives refresh like a real backend would: collection under `tome.mock.collection.v1`, saved decks under `tome.mock.decks.v1`, the working deck under `tome.deck.v1`. Mock calls add small artificial latency so loading states are exercised for real.

## Working deck vs. saved decks

The deck builder edits one **working deck**, persisted via `src/lib/working-deck.ts` (`tome.deck.v1` — always local state, mock or not; it's an editor draft, not backend data). Saving snapshots it as a `SavedDeck` through the decks API; the working deck then carries `deckId`/`name` so later saves update the same record instead of duplicating it. The `/decks` page opens a deck by writing it into the working-deck slot and navigating to `/deck-builder` (confirming first if a *different* unsaved deck would be replaced) — go through `working-deck.ts` for any new surface that hands a deck to the builder, don't touch the localStorage key directly.

## Shared filter logic and UI (do not re-duplicate)

- **`src/lib/filter-cards.ts`** (`CollectionFilters`, `DEFAULT_FILTERS`, `applyFilters`) is the single source of truth for collection filtering/sorting. It is consumed by both the `/collection` page and the deck builder's `CollectionPanel`. This logic used to be duplicated and was deliberately unified — keep it that way.
- **`src/components/collection/CollectionFilterControls.tsx`** is the one component rendering the search/color/type/sort controls, reused by `CollectionToolbar` (full collection page) and `CollectionPanel` (condensed deck-builder sidebar). If a third surface needs filters, extend this component — don't fork it.

## Drag-and-drop contract

Card drag-and-drop uses **native HTML5 DnD — no library**. The contract is a custom MIME type:

```ts
export const CARD_DRAG_TYPE = "application/tome-card-id"; // CollectionPanel.tsx
```

Drag sources set the card id under that type (plus `text/plain` with the card name); drop targets check `dataTransfer.types.includes(CARD_DRAG_TYPE)` before accepting. Currently: `CollectionPanel` tiles are sources, the deck board in `src/pages/DeckBuilder.tsx` is the target. New drop targets (e.g. dropping onto a specific role column, or the commander slot) should extend this contract — don't pull in dnd-kit for a one-off.

## Design system

- **Tailwind** with semantic tokens in `tailwind.config.ts`. Dark-only, "ancient archive meets modern AI research lab": `bg` warm charcoal, `panel` espresso, `ink` parchment text, `ink-muted`, `line` bronze-gray borders. Accents are role-scoped and used sparingly: `action` royal blue (primary actions), `ai` muted violet (AI-generated content), `gold` (commander highlights), `success` emerald, `warn` amber. Card art supplies the color; the UI stays understated — no textures, ornaments, or glow. Inter is self-hosted via `@fontsource-variable/inter` (imported in `main.tsx`); `globals.css` maps it onto the `--font-inter` variable that `tailwind.config.ts` resolves for `font-sans`.
- Icons are inline SVG (never emoji). Interactive elements get `cursor-pointer`, visible `focus-visible` outlines, and 150–300ms transitions; `prefers-reduced-motion` is honored globally in `globals.css`.
- Modals use native `<dialog>` (`showModal()`) for free focus trapping, Esc, and backdrop handling.

## Gotchas

- **Card images are plain `<img>` pointing straight at Scryfall's CDN** — deliberately not proxied or optimized server-side. Scryfall rejects requests without a `User-Agent`, which any Node-side image proxy would trip over; browsers hit the CDN directly without issue. Keep the `absolute inset-0 h-full w-full object-cover` pattern inside a `relative` ancestor when adding new card imagery.
- **`src/pages/` is not a router.** Adding a file there does nothing until it's wired into the route table in `src/App.tsx`.
- **The `@/*` alias is declared twice** — `tsconfig.json` for the type-checker and `resolve.alias` in `vite.config.ts` for the bundler. Changing one without the other gives you code that type-checks but won't build (or vice versa).

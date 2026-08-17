import { Link, Route, Routes } from "react-router";
import { SideNav } from "@/components/ui/SideNav";
import { Home } from "@/pages/Home";
import { Collection } from "@/pages/Collection";
import { DeckBuilder } from "@/pages/DeckBuilder";
import { Decks } from "@/pages/Decks";

// The persistent app chrome plus the route table. Routes are declared here
// explicitly — a file under pages/ isn't reachable until it's listed below.
export function App() {
  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      {/* Collapses to a horizontal bar on small screens. */}
      <aside className="z-40 flex shrink-0 items-center gap-6 border-b border-line bg-bg px-4 py-3 md:sticky md:top-0 md:h-screen md:w-56 md:flex-col md:items-stretch md:gap-8 md:border-b-0 md:border-r md:px-4 md:py-6">
        <Link
          to="/"
          className="text-lg font-semibold tracking-tight text-ink transition-colors hover:text-gold focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action md:px-3"
        >
          Tome
        </Link>
        <SideNav />
      </aside>
      <div className="min-w-0 flex-1">
        <Routes>
          <Route index element={<Home />} />
          <Route path="/collection" element={<Collection />} />
          <Route path="/deck-builder" element={<DeckBuilder />} />
          <Route path="/decks" element={<Decks />} />
        </Routes>
      </div>
    </div>
  );
}

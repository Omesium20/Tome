import Link from "next/link";

export default function HomePage() {
  return (
    <main className="mx-auto flex max-w-screen-md flex-col items-start px-6 pt-24">
      <h1 className="text-4xl font-semibold tracking-tight">Tome</h1>
      <p className="mt-4 max-w-lg text-lg leading-relaxed text-ink-muted">
        An AI-powered Commander deck-building assistant. Browse your collection,
        pick the cards you want to build around, and let Tome construct the rest
        of the deck.
      </p>
      <Link
        href="/collection"
        className="mt-8 rounded-lg bg-action px-5 py-2.5 text-sm font-medium text-white transition-colors duration-150 hover:bg-action/85 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action"
      >
        View your collection
      </Link>
      <p className="mt-3 text-sm text-ink-muted/70">
        Deck building is coming next — it will draw from your collection.
      </p>
    </main>
  );
}

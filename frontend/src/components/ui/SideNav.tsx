import { NavLink } from "react-router";

const links = [
  { href: "/collection", label: "Collection" },
  { href: "/deck-builder", label: "Deck Builder" },
  { href: "/decks", label: "Decks" },
];

export function SideNav() {
  return (
    <nav className="flex items-center gap-1 md:flex-col md:items-stretch">
      {links.map(({ href, label }) => (
        // NavLink's default (non-`end`) prefix matching is what the manual
        // pathname.startsWith() check used to do.
        <NavLink
          key={href}
          to={href}
          className={({ isActive }) =>
            `rounded-lg px-3 py-2 text-sm transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action ${
              isActive
                ? "bg-panel-raised font-medium text-ink"
                : "text-ink-muted hover:bg-panel hover:text-ink"
            }`
          }
        >
          {label}
        </NavLink>
      ))}
    </nav>
  );
}

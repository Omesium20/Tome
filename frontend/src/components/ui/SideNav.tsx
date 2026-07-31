"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const links = [
  { href: "/collection", label: "Collection" },
  { href: "/deck-builder", label: "Deck Builder" },
];

export function SideNav() {
  const pathname = usePathname();

  return (
    <nav className="flex items-center gap-1 md:flex-col md:items-stretch">
      {links.map(({ href, label }) => {
        const active = pathname.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? "page" : undefined}
            className={`rounded-lg px-3 py-2 text-sm transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action ${
              active
                ? "bg-panel-raised font-medium text-ink"
                : "text-ink-muted hover:bg-panel hover:text-ink"
            }`}
          >
            {label}
          </Link>
        );
      })}
    </nav>
  );
}

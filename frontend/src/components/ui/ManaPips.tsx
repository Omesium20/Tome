// Renders a Scryfall-style mana cost ("{2}{G}{G}") as colored pips.

const PIP_CLASSES: Record<string, string> = {
  W: "bg-mana-w text-stone-800",
  U: "bg-mana-u text-white",
  B: "bg-mana-b text-stone-900",
  R: "bg-mana-r text-white",
  G: "bg-mana-g text-white",
};

function parsePips(manaCost: string): string[] {
  return Array.from(manaCost.matchAll(/\{([^}]+)\}/g), (m) => m[1]);
}

export function ManaPips({
  manaCost,
  className = "",
}: {
  manaCost: string;
  className?: string;
}) {
  const pips = parsePips(manaCost);
  if (pips.length === 0) return null;

  return (
    <span
      className={`inline-flex items-center gap-0.5 ${className}`}
      aria-label={`Mana cost: ${pips.join(", ")}`}
    >
      {pips.map((pip, i) => (
        <span
          key={`${pip}-${i}`}
          aria-hidden="true"
          className={`inline-flex h-4 w-4 items-center justify-center rounded-full text-[10px] font-semibold leading-none ${
            PIP_CLASSES[pip] ?? "bg-mana-c text-stone-800"
          }`}
        >
          {pip.length > 1 ? pip.replace("/", "") : pip}
        </span>
      ))}
    </span>
  );
}

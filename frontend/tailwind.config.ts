import type { Config } from "tailwindcss";

// Palette: "ancient archive meets modern AI research lab" — dark only.
// Card art supplies the color; accents are used sparingly by role.
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#1E1B18", // warm charcoal page background
        panel: "#2A2622", // espresso panels/surfaces
        "panel-raised": "#332E29", // hover / raised surfaces
        ink: "#F2EBDD", // parchment primary text
        "ink-muted": "#C6B8A8", // warm gray secondary text
        line: "#4A433D", // bronze-gray borders
        action: "#3B6FE0", // royal blue — primary actions
        ai: "#8B7EC8", // muted violet — AI-generated insights
        gold: "#C9A227", // antique gold — commander highlights
        success: "#3E9B6E", // emerald
        warn: "#D99A2B", // amber
        mana: {
          w: "#E8E4D5",
          u: "#4A90C2",
          b: "#8B7F93",
          r: "#C4553E",
          g: "#5B9367",
          c: "#B4AEA5",
        },
      },
      fontFamily: {
        sans: ["var(--font-inter)", "system-ui", "sans-serif"],
      },
      aspectRatio: {
        card: "5 / 7",
      },
    },
  },
  plugins: [],
};

export default config;

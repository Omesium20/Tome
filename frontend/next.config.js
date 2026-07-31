/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  images: {
    // Scryfall rejects requests without a User-Agent header, which breaks the
    // Next image-optimizer proxy (Node fetch sends none). The browser loads
    // Scryfall's CDN (already sized/compressed) fine directly, so skip
    // server-side optimization.
    unoptimized: true,
    remotePatterns: [
      {
        protocol: "https",
        hostname: "cards.scryfall.io",
        pathname: "/**",
      },
    ],
  },
};

module.exports = nextConfig;

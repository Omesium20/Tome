import type { Metadata } from "next";
import { Inter } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "Tome — Commander Deck Builder",
  description: "AI-powered Magic: The Gathering Commander deck-building assistant",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={inter.variable}>
      <body className="min-h-screen font-sans">
        <header className="sticky top-0 z-40 border-b border-line bg-bg/95 backdrop-blur-sm">
          <nav className="mx-auto flex h-14 max-w-screen-2xl items-center gap-8 px-4 sm:px-6">
            <Link
              href="/"
              className="text-lg font-semibold tracking-tight text-ink transition-colors hover:text-gold focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action"
            >
              Tome
            </Link>
            <div className="flex items-center gap-6 text-sm">
              <Link
                href="/collection"
                className="text-ink-muted transition-colors hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action"
              >
                Collection
              </Link>
              <span
                className="cursor-not-allowed text-ink-muted/50"
                title="Coming soon"
              >
                Deck Builder
              </span>
            </div>
          </nav>
        </header>
        {children}
      </body>
    </html>
  );
}

import type { Metadata } from "next";
import { Inter } from "next/font/google";
import Link from "next/link";
import { SideNav } from "@/components/ui/SideNav";
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
        <div className="flex min-h-screen flex-col md:flex-row">
          {/* Collapses to a horizontal bar on small screens. */}
          <aside className="z-40 flex shrink-0 items-center gap-6 border-b border-line bg-bg px-4 py-3 md:sticky md:top-0 md:h-screen md:w-56 md:flex-col md:items-stretch md:gap-8 md:border-b-0 md:border-r md:px-4 md:py-6">
            <Link
              href="/"
              className="text-lg font-semibold tracking-tight text-ink transition-colors hover:text-gold focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action md:px-3"
            >
              Tome
            </Link>
            <SideNav />
          </aside>
          <div className="min-w-0 flex-1">{children}</div>
        </div>
      </body>
    </html>
  );
}

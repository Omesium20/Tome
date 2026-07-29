import type { Metadata } from "next";
import "./globals.css";

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
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

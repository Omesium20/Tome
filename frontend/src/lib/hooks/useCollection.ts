"use client";

// Shared SWR cache for the collection: /collection and the deck builder both
// need the full LibraryCard[] list, and previously each fetched (and
// re-fetched on every mount) independently. Keying on a fixed string dedupes
// concurrent requests and caches the result across page navigations.

import useSWR from "swr";
import type { LibraryCard } from "@/lib/types";
import { getCollection } from "@/lib/api";

const COLLECTION_KEY = "collection";

export function useCollection() {
  const { data, error, isLoading, mutate } = useSWR<LibraryCard[]>(
    COLLECTION_KEY,
    getCollection,
  );

  return {
    collection: data ?? null,
    error: error instanceof Error ? error.message : error ? String(error) : null,
    isLoading,
    mutate,
  };
}

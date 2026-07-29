import type { Deck, DeckGenerationRequest } from "./types";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });

  if (!res.ok) {
    throw new Error(`Request to ${path} failed: ${res.status} ${res.statusText}`);
  }

  return res.json() as Promise<T>;
}

export function generateDeck(payload: DeckGenerationRequest): Promise<Deck> {
  return request<Deck>("/api/decks/generate", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function uploadCollection(file: File): Promise<{ imported: number }> {
  const formData = new FormData();
  formData.append("file", file);

  return fetch(`${API_BASE_URL}/api/collection/import`, {
    method: "POST",
    body: formData,
  }).then((res) => {
    if (!res.ok) {
      throw new Error(`Collection import failed: ${res.status} ${res.statusText}`);
    }
    return res.json();
  });
}

"""Embeds knowledge documents with a Hugging Face Sentence Transformer and writes to ChromaDB.

See docs/architecture.md#knowledge-pipeline (step 5-6).
"""


def build_embeddings() -> None:
    raise NotImplementedError


if __name__ == "__main__":
    build_embeddings()

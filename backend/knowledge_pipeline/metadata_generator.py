"""Uses Claude to generate CardMetadata (roles, themes, synergy tags, ...) for each Card.

See docs/architecture.md#knowledge-pipeline (step 2) and docs/data-model.md#cardmetadata.
"""


def generate_metadata() -> None:
    raise NotImplementedError


if __name__ == "__main__":
    generate_metadata()

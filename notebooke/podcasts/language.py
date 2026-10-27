"""Normalize UI labels and legacy profiles to podcast-creator language codes."""


def normalize_podcast_language(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    code = value.strip().replace("_", "-")
    code = {
        "vietnamese": "vi",
        "tiếng việt": "vi",
        "tieng viet": "vi",
        "english": "en",
        "tiếng anh": "en",
    }.get(code.lower(), code)
    from podcast_creator.language import resolve_language_name

    resolve_language_name(code)
    return code

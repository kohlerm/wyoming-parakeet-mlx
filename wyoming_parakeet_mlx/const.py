"""Constants for wyoming-parakeet-mlx."""

# Default model to use
DEFAULT_MODEL = "mlx-community/parakeet-tdt-0.6b-v3"

# Streaming defaults
DEFAULT_STREAM_CONTEXT_SIZE = (128, 64)
DEFAULT_STREAM_DEPTH = 1

# Default port
DEFAULT_PORT = 10301

# Parakeet v3 supported languages
# The v3 model claims multilingual support for these languages,
# though English is by far the strongest.
PARAKEET_LANGUAGES = [
    "en",  # English (primary, best accuracy)
    "de",  # German
    "es",  # Spanish
    "fr",  # French
    "it",  # Italian
    "pt",  # Portuguese
    "nl",  # Dutch
    "pl",  # Polish
    "cs",  # Czech
    "sk",  # Slovak
    "hu",  # Hungarian
    "ro",  # Romanian
    "bg",  # Bulgarian
    "hr",  # Croatian
    "sl",  # Slovenian
    "et",  # Estonian
    "lv",  # Latvian
    "lt",  # Lithuanian
    "da",  # Danish
    "sv",  # Swedish
    "fi",  # Finnish
    "el",  # Greek
    "mt",  # Maltese
    "ru",  # Russian
    "uk",  # Ukrainian
]

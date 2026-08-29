import re
from pathlib import Path

import pytest

from arca.i18n import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, load_catalog, normalize_language

EXPECTED_DIAGNOSTIC_KEYS = {
    "library_path": ("ok", "broken"),
    "free_space": ("ok", "degraded", "broken"),
    "write_access": ("ok", "degraded"),
    "clock": ("ok", "degraded", "broken"),
}


def test_supported_languages_are_spanish_and_english() -> None:
    assert SUPPORTED_LANGUAGES == ("es", "en")
    assert DEFAULT_LANGUAGE == "es"


@pytest.mark.parametrize("value", [None, "", "fr", "ES", "de"])
def test_normalize_language_falls_back_to_default(value: str | None) -> None:
    assert normalize_language(value) == DEFAULT_LANGUAGE


def test_normalize_language_keeps_supported_values() -> None:
    assert normalize_language("en") == "en"
    assert normalize_language("es") == "es"


def test_catalogs_share_the_same_key_set() -> None:
    keys_by_language = {lang: set(load_catalog(lang).messages) for lang in SUPPORTED_LANGUAGES}
    assert keys_by_language["es"] == keys_by_language["en"]
    assert keys_by_language["es"]


def test_catalogs_have_no_empty_messages() -> None:
    for lang in SUPPORTED_LANGUAGES:
        for key, value in load_catalog(lang).messages.items():
            assert value.strip(), f"{lang}:{key}"


def test_diagnostic_keys_exist_for_every_reachable_state() -> None:
    for lang in SUPPORTED_LANGUAGES:
        catalog = load_catalog(lang)
        for check_id, states in EXPECTED_DIAGNOSTIC_KEYS.items():
            for state in states:
                assert catalog.has(f"diag.{check_id}.{state}.summary"), f"{lang}:{check_id}:{state}"
                if state != "ok":
                    assert catalog.has(f"diag.{check_id}.{state}.impact")
                    assert catalog.has(f"diag.{check_id}.{state}.action")


def test_text_formats_named_params() -> None:
    catalog = load_catalog("es")
    rendered = catalog.text("home.search.empty", query="X:/lib")
    assert "X:/lib" in rendered


def test_text_returns_key_when_missing() -> None:
    assert load_catalog("en").text("no.such.key") == "no.such.key"


def test_text_survives_missing_params() -> None:
    catalog = load_catalog("en")
    assert catalog.text("home.search.empty") == catalog.messages["home.search.empty"]


def test_unsupported_language_loads_default_catalog() -> None:
    assert load_catalog("fr").language == DEFAULT_LANGUAGE


STATIC_DIR = Path(__file__).resolve().parents[1] / "arca" / "static"
TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "arca" / "templates"
DYNAMIC_PREFIXES = (
    "admin.downloads.status.",
    "admin.vendor.state.",
    "admin.vendor.drift.",
    "admin.status.worst.",
    "admin.content.state.",
    "admin.content.index.",
    "home.sources.",
    "home.seal.kind.",
    "home.health.",
    "admin.engine.state.",
)


def _template_keys() -> set[str]:
    keys: set[str] = set()
    for template in TEMPLATE_DIR.glob("*.html"):
        text = template.read_text("utf-8")
        keys.update(re.findall(r'data-i18n(?:-placeholder|-title|-label)?="([^"]+)"', text))
    return keys


def _script_keys() -> set[str]:
    keys: set[str] = set()
    for script in STATIC_DIR.glob("*.js"):
        text = script.read_text("utf-8")
        for key in re.findall(r'(?<![\w.])t\(\s*"([^"]+)"', text):
            if not key.endswith("."):
                keys.add(key)
        keys.update(re.findall(r'error(?:Text|Detail)\(\s*"([^"]+)"', text))
    return keys


def test_every_key_used_by_the_interface_exists_in_both_catalogs() -> None:
    used = _template_keys() | _script_keys()
    assert used
    for lang in SUPPORTED_LANGUAGES:
        catalog = load_catalog(lang)
        missing = {key for key in used if not catalog.has(key)}
        assert not missing, f"{lang}: {sorted(missing)}"


def test_dynamic_key_families_are_complete_in_both_catalogs() -> None:
    for lang in SUPPORTED_LANGUAGES:
        keys = set(load_catalog(lang).messages)
        for prefix in DYNAMIC_PREFIXES:
            assert any(key.startswith(prefix) for key in keys), f"{lang}:{prefix}"


def test_the_interface_never_hardcodes_a_user_facing_string_from_a_catalog() -> None:
    spanish = load_catalog("es").messages
    long_strings = {value for value in spanish.values() if len(value) > 24}
    for script in STATIC_DIR.glob("*.js"):
        text = script.read_text("utf-8")
        for value in long_strings:
            assert value not in text, f"{script.name}: {value}"


API_SOURCE = Path(__file__).resolve().parents[1] / "arca" / "api.py"
ERROR_CODE_KEYS = {
    "admin.catalog.error": ("unreachable",),
    "admin.catalog.filters_error": ("unreachable",),
    "admin.catalog.add_error": ("unreachable",),
    "admin.llama.error": (
        "no_binary",
        "no_profile",
        "unknown_profile",
        "invalid_name",
        "unknown_mode",
    ),
    "admin.hub.error": (
        "gated",
        "unreachable",
        "bad_model_id",
        "bad_kind",
        "bad_file",
        "unknown_file",
    ),
    "admin.vendor.llama_error": ("bad_commit",),
    "admin.documents.upload_error": ("bad_file", "too_large"),
}


def test_every_error_code_the_api_returns_has_an_actionable_message() -> None:
    emitted = set(re.findall(r'"code": "([a-z_]+)"', API_SOURCE.read_text("utf-8")))
    covered = {code for codes in ERROR_CODE_KEYS.values() for code in codes}
    assert emitted == covered
    for lang in SUPPORTED_LANGUAGES:
        catalog = load_catalog(lang)
        for family, codes in ERROR_CODE_KEYS.items():
            assert catalog.has(family), f"{lang}:{family}"
            for code in codes:
                key = f"{family}.{code}"
                assert catalog.has(key), f"{lang}:{key}"
                assert "{error}" not in catalog.messages[key], f"{lang}:{key}"


def test_every_bringup_state_has_a_message_in_both_catalogs() -> None:
    from arca import engine

    states = {
        value
        for name, value in vars(engine).items()
        if name.startswith("STATE_") and isinstance(value, str)
    }
    assert len(states) > 5
    for lang in SUPPORTED_LANGUAGES:
        catalog = load_catalog(lang)
        for state in sorted(states):
            assert catalog.has(f"admin.engine.state.{state}"), f"{lang}:{state}"

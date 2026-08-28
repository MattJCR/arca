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
    rendered = catalog.text("status.root", root="X:/lib")
    assert "X:/lib" in rendered


def test_text_returns_key_when_missing() -> None:
    assert load_catalog("en").text("no.such.key") == "no.such.key"


def test_text_survives_missing_params() -> None:
    catalog = load_catalog("en")
    assert catalog.text("status.root") == catalog.messages["status.root"]


def test_unsupported_language_loads_default_catalog() -> None:
    assert load_catalog("fr").language == DEFAULT_LANGUAGE

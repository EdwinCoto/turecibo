import os

from services import vision


def test_get_client_config_for_github_models_token(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "ghp_exampletoken")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("GITHUB_MODELS_API_VERSION", raising=False)

    base_url, headers, model = vision._get_client_config()

    assert base_url == "https://models.github.ai/inference"
    assert headers == {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
    }
    assert model == "openai/gpt-4.1"


def test_get_client_config_for_openai_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("GITHUB_MODELS_API_VERSION", raising=False)

    base_url, headers, model = vision._get_client_config()

    assert base_url is None
    assert headers == {}
    assert model == "gpt-4o"


def test_normalize_extraction_payload_maps_documento_to_dni():
    payload = {
        "restaurant_name": "Demo",
        "documento": "DOCUMENTO: 72804567",
    }

    normalized = vision._normalize_extraction_payload(payload)

    assert normalized["dni"] == "72804567"


def test_normalize_extraction_payload_maps_dotted_dni_key():
    payload = {
        "D.N.I": "72804567",
    }

    normalized = vision._normalize_extraction_payload(payload)

    assert normalized["dni"] == "72804567"


def test_normalize_dni_value_rejects_non_eight_digit_values():
    assert vision._normalize_dni_value("DOC 1234567") is None
    assert vision._normalize_dni_value("DOC 123456789") is None


def test_normalize_extraction_payload_maps_dotted_ruc_key():
    payload = {
        "R.U.C.": "20613724851",
    }

    normalized = vision._normalize_extraction_payload(payload)

    assert normalized["ruc"] == "20613724851"


def test_normalize_extraction_payload_rejects_invalid_ruc():
    payload = {
        "ruc": "00000000000",
    }

    normalized = vision._normalize_extraction_payload(payload)

    assert normalized["ruc"] is None


def test_normalize_extraction_payload_extracts_electronic_receipt_number():
    payload = {
        "electronic_receipt_number": "B130-00274475",
    }

    normalized = vision._normalize_extraction_payload(payload)

    assert normalized["electronic_receipt_number"] == "B130-00274475"


def test_normalize_extraction_payload_rejects_invalid_electronic_receipt_number():
    payload = {
        "electronic_receipt_number": "F130-000123",
    }

    normalized = vision._normalize_extraction_payload(payload)

    assert normalized["electronic_receipt_number"] is None
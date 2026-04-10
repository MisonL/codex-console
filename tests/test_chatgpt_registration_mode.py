from src.core.anyauto.registration_mode import (
    CHATGPT_REGISTRATION_MODE_ACCESS_TOKEN_ONLY,
    CHATGPT_REGISTRATION_MODE_REFRESH_TOKEN,
    resolve_chatgpt_registration_mode,
)


def test_resolve_chatgpt_registration_mode_defaults_to_refresh_token():
    assert resolve_chatgpt_registration_mode() == CHATGPT_REGISTRATION_MODE_REFRESH_TOKEN


def test_resolve_chatgpt_registration_mode_supports_boolean_flag():
    assert (
        resolve_chatgpt_registration_mode(refresh_token_enabled=False)
        == CHATGPT_REGISTRATION_MODE_ACCESS_TOKEN_ONLY
    )
    assert (
        resolve_chatgpt_registration_mode(refresh_token_enabled=True)
        == CHATGPT_REGISTRATION_MODE_REFRESH_TOKEN
    )


def test_resolve_chatgpt_registration_mode_supports_string_aliases():
    assert (
        resolve_chatgpt_registration_mode(value="no_rt")
        == CHATGPT_REGISTRATION_MODE_ACCESS_TOKEN_ONLY
    )
    assert (
        resolve_chatgpt_registration_mode(value="with_rt")
        == CHATGPT_REGISTRATION_MODE_REFRESH_TOKEN
    )

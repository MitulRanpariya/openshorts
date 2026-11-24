"""clipforge_config: one YAML file mapped onto the env vars the code reads."""

from pathlib import Path

import pytest

import clipforge_config as cc

REPO = Path(__file__).resolve().parent.parent


def write(tmp_path, text):
    p = tmp_path / "clipforge.config.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_missing_file_is_empty(tmp_path):
    assert cc.load(tmp_path / "nope.yaml") == {}
    env = {}
    assert cc.apply(tmp_path / "nope.yaml", env) == []
    assert env[cc.APPLIED_MARKER] == "1"


def test_maps_nested_keys_to_env_names(tmp_path):
    p = write(tmp_path, """
llm:
  gemini: {api_key: g-key-123, model: gemini-x}
  openai_compatible: {base_url: "http://localhost:11434/v1", score_batch: 4}
upload_post: {api_key: up-key-456, profile: me}
processing: {max_concurrent_jobs: 1}
clips: {max_seconds: 90}
""")
    assert cc.load(p) == {
        "GEMINI_API_KEY": "g-key-123",
        "GEMINI_MODEL": "gemini-x",
        "LLM_BASE_URL": "http://localhost:11434/v1",
        "LLM_SCORE_BATCH": "4",
        "UPLOAD_POST_API_KEY": "up-key-456",
        "UPLOAD_POST_PROFILE": "me",
        "MAX_CONCURRENT_JOBS": "1",
        "CLIP_MAX_SECONDS": "90",
    }


def test_real_env_wins_and_empty_values_are_skipped(tmp_path):
    p = write(tmp_path, 'llm:\n  gemini: {api_key: from-file, model: ""}\nfal: {api_key:}\n')
    env = {"GEMINI_API_KEY": "from-env"}
    assert cc.apply(p, env) == []
    assert env["GEMINI_API_KEY"] == "from-env"
    assert "GEMINI_MODEL" not in env and "FAL_KEY" not in env


def test_child_that_inherited_the_server_env_does_not_reread(tmp_path):
    p = write(tmp_path, "upload_post: {api_key: secret-upload}\n")
    env = {cc.APPLIED_MARKER: "1"}  # the server's child_env stripped the key
    assert cc.apply(p, env) == []
    assert "UPLOAD_POST_API_KEY" not in env


def test_env_passthrough_and_booleans(tmp_path):
    p = write(tmp_path, "env:\n  AUTO_HOOK: true\n  PRO_MODE: off_value\n")
    assert cc.load(p) == {"AUTO_HOOK": "true", "PRO_MODE": "off_value"}


@pytest.mark.parametrize("text", [
    "env:\n  lower_case: 1\n",
    "env: [1, 2]\n",
    "llm:\n  gemini:\n    api_key: [a, b]\n",
    "- just\n- a list\n",
    "llm: {gemini: {api_key: 'unterminated}\n",
])
def test_bad_files_raise_config_error(tmp_path, text):
    with pytest.raises(cc.ConfigError):
        cc.load(write(tmp_path, text))


def test_unknown_key_is_ignored_with_warning(tmp_path, caplog):
    p = write(tmp_path, "llm:\n  gemni: {api_key: typo}\n")
    assert cc.load(p) == {}
    assert "unknown key llm.gemni.api_key" in caplog.text


def test_cookie_file_contents_are_read_relative_to_config(tmp_path):
    (tmp_path / "cookies.txt").write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    p = write(tmp_path, "youtube: {download_cookies_file: cookies.txt}\n")
    assert cc.load(p) == {"YOUTUBE_COOKIES": "# Netscape HTTP Cookie File\n"}
    with pytest.raises(cc.ConfigError):
        cc.load(write(tmp_path, "youtube: {download_cookies_file: missing.txt}\n"))


def test_describe_exposes_booleans_only():
    env = {"GEMINI_API_KEY": "secret-value", "UPLOAD_POST_API_KEY": ""}
    d = cc.describe(env)
    assert d["gemini"] is True and d["uploadPost"] is False
    assert all(isinstance(v, bool) for v in d.values())
    assert "secret-value" not in repr(d)


def test_redact_hides_configured_secrets():
    env = {"GEMINI_API_KEY": "AIzaSecret123", "FAL_KEY": "abc"}
    assert cc.redact("key=AIzaSecret123 fal=abc", env) == "key=*** fal=abc"


def test_example_file_parses_and_only_uses_known_keys(caplog):
    values = cc.load(REPO / "clipforge.config.example.yaml")
    assert values == {}  # every value in the example is empty
    assert "unknown key" not in caplog.text


def test_real_config_file_is_git_ignored():
    ignored = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "clipforge.config.yaml" in ignored


def test_byok_key_prefers_header_then_server_env(monkeypatch):
    import app

    monkeypatch.setenv("FAL_KEY", "server-fal")
    assert app.byok_key("client-fal", "FAL_KEY") == "client-fal"
    assert app.byok_key(None, "FAL_KEY") == "server-fal"
    monkeypatch.delenv("FAL_KEY")
    assert app.byok_key("", "FAL_KEY") is None


def test_post_profile_falls_back_to_configured_profile(monkeypatch):
    import app

    monkeypatch.setenv("UPLOAD_POST_PROFILE", "my-profile")
    assert app.resolve_post_profile(None, None) == "my-profile"
    assert app.resolve_post_profile(None, "chosen") == "chosen"

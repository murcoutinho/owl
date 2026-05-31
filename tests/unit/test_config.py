"""Unit tests for owl.config."""

from __future__ import annotations

from pathlib import Path

from owl.config import Config, LLMSlot, load_env_local, parse_env_file

# ─── parse_env_file primitive ───────────────────────────────────────────────


def test_parse_env_file_basic():
    assert parse_env_file("A=1\nB=hello\n") == {"A": "1", "B": "hello"}


def test_parse_env_file_strips_surrounding_quotes():
    assert parse_env_file('KEY="value with spaces"\n') == {"KEY": "value with spaces"}
    assert parse_env_file("KEY='single quoted'\n") == {"KEY": "single quoted"}


def test_parse_env_file_strips_export_prefix():
    assert parse_env_file("export FOO=bar\n") == {"FOO": "bar"}


def test_parse_env_file_skips_comments_and_blanks():
    text = """
# this is a comment
A=1

   # indented comment
B=2
"""
    assert parse_env_file(text) == {"A": "1", "B": "2"}


def test_parse_env_file_ignores_malformed_lines():
    assert parse_env_file("not-an-env-line\nA=1\n") == {"A": "1"}


# ─── load_env_local merging ─────────────────────────────────────────────────


def test_load_env_local_fills_gaps(tmp_path: Path):
    env_local = tmp_path / ".env.local"
    env_local.write_text("OWL_TARGET_REPOS=saudade\nOWL_IMPL_MODEL=custom\n")
    base = {"OWL_IMPL_MODEL": "from-shell"}
    merged = load_env_local(env_local, base)
    # Shell wins over .env.local for keys already set
    assert merged["OWL_IMPL_MODEL"] == "from-shell"
    # .env.local fills keys missing from shell
    assert merged["OWL_TARGET_REPOS"] == "saudade"


def test_load_env_local_short_circuits_when_skip_flag_set(tmp_path: Path):
    env_local = tmp_path / ".env.local"
    env_local.write_text("OWL_TARGET_REPOS=saudade\n")
    merged = load_env_local(env_local, {"OWL_SKIP_ENV_LOCAL": "1"})
    assert "OWL_TARGET_REPOS" not in merged


def test_load_env_local_no_file_returns_base(tmp_path: Path):
    merged = load_env_local(tmp_path / "nope", {"A": "1"})
    assert merged == {"A": "1"}


# ─── LLMSlot.enabled ────────────────────────────────────────────────────────


def test_llm_slot_enabled_truthy():
    assert LLMSlot(provider="claude", model="claude-sonnet-4-6").enabled is True


def test_llm_slot_disabled_when_provider_none():
    assert LLMSlot(provider="none", model="x").enabled is False


def test_llm_slot_disabled_when_model_blank_or_none_literal():
    assert LLMSlot(provider="claude", model="").enabled is False
    assert LLMSlot(provider="claude", model="none").enabled is False


# ─── Config.from_env ────────────────────────────────────────────────────────


def test_config_target_repos_split_on_whitespace():
    cfg = Config.from_env({"OWL_TARGET_REPOS": "saudade  raven   bem-te-vi"})
    assert cfg.target_repos == ("saudade", "raven", "bem-te-vi")


def test_config_target_repos_empty_when_unset():
    cfg = Config.from_env({})
    assert cfg.target_repos == ()


def test_config_provider_defaults():
    cfg = Config.from_env({"OWL_TARGET_REPOS": "saudade"})
    # Implementer + fixer default to Claude Opus 4.7.
    assert cfg.impl.provider == "claude"
    assert cfg.impl.model == "claude-opus-4-7"
    assert cfg.fix.provider == "claude"
    assert cfg.fix.model == "claude-opus-4-7"
    # Reviewers default to the two Codex models (GPT-5.5 and GPT-5.3-codex).
    assert cfg.reviewer1.provider == "codex"
    assert cfg.reviewer1.model == "gpt-5.5"
    assert cfg.reviewer2.provider == "codex"
    assert cfg.reviewer2.model == "gpt-5.3-codex"


def test_config_fix_provider_defaults_to_impl_when_unset():
    cfg = Config.from_env(
        {"OWL_TARGET_REPOS": "saudade", "OWL_IMPL_PROVIDER": "codex"}
    )
    assert cfg.impl.provider == "codex"
    assert cfg.fix.provider == "codex"


def test_config_reviewer_disable_via_none_provider():
    cfg = Config.from_env(
        {"OWL_TARGET_REPOS": "saudade", "OWL_REVIEWER2_PROVIDER": "none"}
    )
    assert cfg.reviewer2.enabled is False


def test_config_per_repo_test_cmd_with_hyphen_to_underscore():
    """OWL_TEST_CMD_saudade_mobile applies to the repo named ``saudade-mobile``."""
    cfg = Config.from_env(
        {
            "OWL_TARGET_REPOS": "saudade saudade-mobile",
            "OWL_TEST_CMD_saudade": "pytest",
            "OWL_TEST_CMD_saudade_mobile": "npm test",
            "OWL_TEST_SETUP_saudade_mobile": "npm ci",
        }
    )
    assert cfg.test_cmd["saudade"] == "pytest"
    assert cfg.test_cmd["saudade-mobile"] == "npm test"
    assert cfg.test_setup["saudade-mobile"] == "npm ci"


def test_config_review_iterations_clamped_minimum():
    cfg = Config.from_env({"OWL_TARGET_REPOS": "x", "REVIEW_ITERATIONS": "0"})
    assert cfg.review_iterations == 1


def test_config_review_iterations_default_when_missing():
    cfg = Config.from_env({"OWL_TARGET_REPOS": "x"})
    assert cfg.review_iterations == 2
    assert cfg.max_review_rounds == 3


def test_config_review_mode_parallel_is_default():
    cfg = Config.from_env({"OWL_TARGET_REPOS": "x"})
    assert cfg.review_mode == "parallel"


def test_config_review_mode_sequential_recognized():
    cfg = Config.from_env(
        {"OWL_TARGET_REPOS": "x", "OWL_REVIEW_MODE": "sequential"}
    )
    assert cfg.review_mode == "sequential"


def test_config_skip_low_priority_flag():
    cfg = Config.from_env({"OWL_TARGET_REPOS": "x", "OWL_SKIP_LOW_PRIORITY": "1"})
    assert cfg.skip_low_priority is True


def test_config_pr_prefix_default():
    cfg = Config.from_env({"OWL_TARGET_REPOS": "x"})
    assert cfg.pr_prefix == "[owl] "


def test_config_unknown_provider_falls_back_to_claude():
    cfg = Config.from_env({"OWL_TARGET_REPOS": "x", "OWL_IMPL_PROVIDER": "openai"})
    assert cfg.impl.provider == "claude"


def test_config_invalid_int_falls_back_to_default():
    cfg = Config.from_env(
        {"OWL_TARGET_REPOS": "x", "OWL_LLM_TIMEOUT": "not-a-number"}
    )
    assert cfg.llm_timeout == 2400

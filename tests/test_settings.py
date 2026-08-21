import pytest
from pydantic import ValidationError

from flywheel.settings import Settings


def _base(**overrides):
    values = {
        "alpaca_api_key": "k",
        "alpaca_secret_key": "s",
        "alpaca_paper_trade": True,
        "anthropic_api_key": "a",
        "flywheel_env": "dev",
    }
    values.update(overrides)
    return values


def test_paper_trade_true_is_accepted():
    assert Settings(**_base()).alpaca_paper_trade is True


def test_paper_trade_false_is_rejected_at_construction():
    with pytest.raises(ValidationError, match="must be true"):
        Settings(**_base(alpaca_paper_trade=False))


def test_unknown_env_is_rejected():
    with pytest.raises(ValidationError):
        Settings(**_base(flywheel_env="production"))


def test_base_url_is_always_the_paper_endpoint():
    assert Settings(**_base()).alpaca_base_url == "https://paper-api.alpaca.markets"

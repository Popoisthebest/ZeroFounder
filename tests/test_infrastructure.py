from agents.infrastructure import (
    ProductInfrastructureRequirements,
    select_infrastructure,
)
from agents.schemas import InfrastructureProvider


def test_static_product_selects_implemented_github_pages():
    plan = select_infrastructure(ProductInfrastructureRequirements())
    assert plan.provider == InfrastructureProvider.GITHUB_PAGES
    assert plan.implemented
    assert not plan.requires_human_approval


def test_server_data_requires_unimplemented_approved_cloudflare():
    plan = select_infrastructure(ProductInfrastructureRequirements(anonymous_feedback_storage=True))
    assert plan.provider == InfrastructureProvider.CLOUDFLARE_PAGES_WORKERS_D1
    assert not plan.implemented
    assert plan.requires_human_approval


def test_product_ui_language_follows_target_market_not_operating_language(monkeypatch):
    monkeypatch.setenv("OPERATING_LANGUAGE", "ko")
    plan = select_infrastructure(ProductInfrastructureRequirements(target_languages=("ja",)))
    assert plan.provider == InfrastructureProvider.GITHUB_PAGES
    assert plan.product_ui_language == "ja"

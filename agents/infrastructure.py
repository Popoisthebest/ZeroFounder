from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from agents.language import choose_product_ui_language
from agents.schemas import InfrastructureProvider


@dataclass(frozen=True)
class ProductInfrastructureRequirements:
    static_assets: bool = True
    browser_computation: bool = True
    anonymous_feedback_storage: bool = False
    server_api: bool = False
    structured_persistent_data: bool = False
    target_languages: tuple[str, ...] = ("en",)


@dataclass(frozen=True)
class ProviderPlan:
    provider: InfrastructureProvider
    implemented: bool
    requires_human_approval: bool
    rationale: str
    product_ui_language: str
    required_human_actions: tuple[str, ...] = ()


class InfrastructureAdapter(ABC):
    provider: InfrastructureProvider

    @abstractmethod
    def plan(self, requirements: ProductInfrastructureRequirements) -> ProviderPlan: ...


class GitHubPagesAdapter(InfrastructureAdapter):
    provider = InfrastructureProvider.GITHUB_PAGES

    def plan(self, requirements: ProductInfrastructureRequirements) -> ProviderPlan:
        suitable = not any(
            (
                requirements.anonymous_feedback_storage,
                requirements.server_api,
                requirements.structured_persistent_data,
            )
        )
        return ProviderPlan(
            provider=self.provider,
            implemented=True,
            requires_human_approval=not suitable,
            rationale=(
                "MVP가 완전한 정적 제품이며 브라우저에서 동작합니다."
                if suitable
                else "MVP에 GitHub Pages가 제공하지 않는 서버 기능이 필요합니다."
            ),
            product_ui_language=choose_product_ui_language(
                list(requirements.target_languages)
            ),
        )


class CloudflarePagesAdapter(InfrastructureAdapter):
    provider = InfrastructureProvider.CLOUDFLARE_PAGES

    def plan(self, requirements: ProductInfrastructureRequirements) -> ProviderPlan:
        return ProviderPlan(
            provider=self.provider,
            implemented=False,
            requires_human_approval=True,
            rationale=(
                "Cloudflare 계정 연결과 배포는 의도적으로 자동화하지 않습니다."
            ),
            product_ui_language=choose_product_ui_language(
                list(requirements.target_languages)
            ),
            required_human_actions=(
                "Cloudflare 계정과 Pages 프로젝트를 생성하거나 선택합니다.",
                "저장소 연결을 승인하고 비밀값을 commit하지 않고 설정합니다.",
            ),
        )


class CloudflareWorkersD1Adapter(InfrastructureAdapter):
    provider = InfrastructureProvider.CLOUDFLARE_PAGES_WORKERS_D1

    def plan(self, requirements: ProductInfrastructureRequirements) -> ProviderPlan:
        needed = any(
            (
                requirements.anonymous_feedback_storage,
                requirements.server_api,
                requirements.structured_persistent_data,
            )
        )
        return ProviderPlan(
            provider=self.provider,
            implemented=False,
            requires_human_approval=True,
            rationale=(
                "최소 API 또는 구조화된 영속 저장소가 명시적인 MVP 요구사항입니다."
                if needed
                else "이 정적 MVP에는 Workers와 D1이 필요하지 않습니다."
            ),
            product_ui_language=choose_product_ui_language(
                list(requirements.target_languages)
            ),
            required_human_actions=(
                "확장된 인프라와 데이터 처리 범위를 승인합니다.",
                "Worker, D1 데이터베이스와 binding을 수동 생성합니다.",
                "승인된 Cloudflare 비밀값을 저장소 설정에 추가합니다.",
            ),
        )


def select_infrastructure(requirements: ProductInfrastructureRequirements) -> ProviderPlan:
    if any(
        (
            requirements.anonymous_feedback_storage,
            requirements.server_api,
            requirements.structured_persistent_data,
        )
    ):
        return CloudflareWorkersD1Adapter().plan(requirements)
    return GitHubPagesAdapter().plan(requirements)

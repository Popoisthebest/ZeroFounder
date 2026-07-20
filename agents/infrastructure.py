from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from agents.schemas import InfrastructureProvider


@dataclass(frozen=True)
class ProductInfrastructureRequirements:
    static_assets: bool = True
    browser_computation: bool = True
    anonymous_feedback_storage: bool = False
    server_api: bool = False
    structured_persistent_data: bool = False


@dataclass(frozen=True)
class ProviderPlan:
    provider: InfrastructureProvider
    implemented: bool
    requires_human_approval: bool
    rationale: str
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
                "The MVP is fully static and works in the browser."
                if suitable
                else "The MVP requires server-side capabilities GitHub Pages cannot provide."
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
                "Cloudflare account connection and deployment are intentionally not automated."
            ),
            required_human_actions=(
                "Create or select a Cloudflare account and Pages project.",
                "Approve repository connection and configure secrets without committing values.",
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
                "A minimal API or structured persistence is an explicit MVP requirement."
                if needed
                else "Workers and D1 are unnecessary for this static MVP."
            ),
            required_human_actions=(
                "Approve the expanded infrastructure and data-handling scope.",
                "Create the Worker, D1 database, and bindings manually.",
                "Add approved Cloudflare secrets in repository settings.",
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

from __future__ import annotations

from agents.schemas import CompanyState, InfrastructureProvider, LifecycleStage


def initial_company_state() -> CompanyState:
    """Return the immutable bootstrap defaults for a newly initialized repository."""
    return CompanyState(
        lifecycle_stage=LifecycleStage.DISCOVERY,
        autonomy_level=1,
        selected_venture=None,
        active_experiment=None,
        infrastructure_provider=InfrastructureProvider.UNSELECTED,
        sleep_mode=False,
        consecutive_failures=0,
        last_agent_run=None,
        paused_from=None,
        validation_started_at=None,
        deployed_at=None,
    )

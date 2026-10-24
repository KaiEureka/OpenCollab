from __future__ import annotations

from typing import Any

from opencollab.adapters.session_environments import SessionEnvironmentPool
from opencollab.application.isolation import isolation_name
from opencollab.bootstrap.team_config import load_team_config


async def prepare_scheduler(ctx: Any, *, builder: Any, isolation=None,
                            environment_providers=None, **kwargs: Any) -> Any:
    team = kwargs.get("resolved_team_config") or load_team_config(
        ctx.workspace, path=kwargs.get("team_config_path"),
    )
    selected = isolation_name(isolation, default=team.isolation)
    roles = {name: role.isolation for name, role in team.roles.items() if role.isolation is not None}
    if selected is None and not roles:
        return builder(ctx, **kwargs)
    pool = SessionEnvironmentPool(
        ctx.workspace, isolation=selected or ("worktree" if kwargs["use_worktrees"] else "shared"),
        base_environment=kwargs.get("environment"), providers=environment_providers, roles=roles,
        owned_save_dir=kwargs.get("save_dir"),
        enable_adoption=any("adopt_agent" in role.tools for role in team.roles.values()),
        owned_output_paths=getattr(ctx.tracer, "owned_paths", ()),
    )
    try:
        # An unset strategy keeps the historical entry workspace even when a child overrides it.
        entry = await pool.acquire(team.entry, isolation=roles.get(team.entry) or selected or "shared")
        pool.set_source(entry)
        scheduler = builder(ctx, **{**kwargs, "environment": entry, "worktree_pool": pool,
                                    "resolved_team_config": team})
        scheduler.preserve_entry_changes = (roles.get(team.entry) or selected or "shared") != "shared"
        return scheduler
    except BaseException:
        await pool.release()
        raise

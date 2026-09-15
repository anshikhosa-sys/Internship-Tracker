"""
Configuration: data only, no logic.

    taxonomy   skills, role families, seniority markers (the world, not a user)
    companies  industries, sizes, parent companies, application limits
    profile    how résumé evidence becomes a derived profile
    scoring    the scoring model's shape and neutral values
    semantic   local embedding model and vector store
    llm        local model backend and cache
    settings   paths, dashboard defaults, notifications, schedule

`settings` is re-exported so `config.DATABASE_PATH` style reads (and test
overrides of them) keep working.
"""

from .settings import *  # noqa: F401,F403

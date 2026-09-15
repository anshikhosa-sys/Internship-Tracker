"""
Configuration: data only, no logic.

Submodules group the data by subsystem (taxonomy, profile, scoring, companies,
llm, ...). `legacy` holds the single-user tuning the v1 scorer still reads; it
is re-exported here so existing `config.X` reads and test overrides keep
working until that scorer is replaced.
"""

from .legacy import *  # noqa: F401,F403

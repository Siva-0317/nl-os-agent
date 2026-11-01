"""daemon package: safe LLM command proposal and verified commit pipeline."""
from nlagent.daemon.commit import commit, default_shell_runner
from nlagent.daemon.propose import (
    DEFAULT_L0_SYSTEM_PROMPT,
    extract_json_proposal,
    get_default_env,
    propose,
)

__all__ = [
    "propose",
    "commit",
    "extract_json_proposal",
    "get_default_env",
    "DEFAULT_L0_SYSTEM_PROMPT",
    "default_shell_runner",
]

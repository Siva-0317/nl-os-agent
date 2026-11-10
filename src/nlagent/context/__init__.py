"""context package."""
from nlagent.context.grounding import (
    GroundedFile,
    GroundingConfig,
    ground_request,
    search_files,
)
from nlagent.context.layout import PromptParts, build_prompt, format_l1, format_l2
from nlagent.context.profile import Suggestion, UserProfile, get_default_profile_path
from nlagent.context.sanitize import (
    format_files_block,
    format_grounded_file_entry,
    sanitize_filename,
    wrap_file_delimiter,
)
from nlagent.context.state import EffectRecord, Entity, SessionState, count_tokens_approx

__all__ = [
    # Layout & Prompting
    "PromptParts",
    "build_prompt",
    "format_l1",
    "format_l2",
    # State
    "SessionState",
    "Entity",
    "EffectRecord",
    "count_tokens_approx",
    # Grounding
    "GroundedFile",
    "GroundingConfig",
    "search_files",
    "ground_request",
    # Profile
    "UserProfile",
    "Suggestion",
    "get_default_profile_path",
    # Sanitization
    "sanitize_filename",
    "wrap_file_delimiter",
    "format_grounded_file_entry",
    "format_files_block",
]

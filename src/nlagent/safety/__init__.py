"""Safety modules: effect analysis, static inspection, manifest diffing, baseline G1, quarantine rollback, and plain-English renderer."""
from nlagent.safety.baseline_g1 import RiskTier, classify_risk
from nlagent.safety.cmd_parser import CmdInvocation, parse_cmd_line
from nlagent.safety.effects import Effect, analyse, preview, tier_from_effect
from nlagent.safety.manifest import diff, manifest, tier_from_diff
from nlagent.safety.ps_parser import (
    ParsedPSCommand,
    PSParseResult,
    get_ps_parser,
    parse_powershell,
)
from nlagent.safety.quarantine import (
    NameClashError,
    QuarantineLimitExceededError,
    QuarantineManifest,
    QuarantineNotFoundError,
    UndoResult,
    purge,
    quarantine_effect,
    undo,
)
from nlagent.safety.overlay import (
    VirtualFS,
    VirtualNode,
    analyse_on_overlay,
)
from nlagent.safety.renderer import (
    EffectSummary,
    ExecutionPlan,
    format_bytes_human,
    render_full_preview,
    render_plain_english_consequence,
    render_reversibility_description,
    summarize_directory_scopes,
)

__all__ = [
    "Effect",
    "analyse",
    "tier_from_effect",
    "preview",
    "manifest",
    "diff",
    "tier_from_diff",
    # Windows native parsers
    "parse_cmd_line",
    "CmdInvocation",
    "parse_powershell",
    "PSParseResult",
    "ParsedPSCommand",
    "get_ps_parser",
    # Labelled Baseline G1 for benchmark comparison
    "RiskTier",
    "classify_risk",
    # Quarantine and Undo
    "quarantine_effect",
    "undo",
    "purge",
    "QuarantineManifest",
    "UndoResult",
    "QuarantineLimitExceededError",
    "NameClashError",
    "QuarantineNotFoundError",
    # Plain-English Consequence Renderer
    "EffectSummary",
    "ExecutionPlan",
    "format_bytes_human",
    "render_plain_english_consequence",
    "render_reversibility_description",
    "render_full_preview",
    "summarize_directory_scopes",
    # Virtual Filesystem Overlay (Task T36)
    "VirtualFS",
    "VirtualNode",
    "analyse_on_overlay",
]

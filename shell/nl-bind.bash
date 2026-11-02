#!/usr/bin/env bash
# ==============================================================================
# nl-bind.bash - Native Terminal Keybinding (Ctrl+G) for Bash
#
# Usage:
#   Add the following line to your ~/.bashrc:
#     source /path/to/nl-os-agent/shell/nl-bind.bash
#
# When you type a natural language instruction and press Ctrl+G:
# 1. Calls `nlctl propose "<buffer>" --emit-shell`.
# 2. Displays colored risk tier (Green/Yellow/Red) and predicted effect preview on stderr.
# 3. Places `eval "$(nlctl commit <id> --emit-shell)"` on the command line buffer for review.
# ==============================================================================

nl_hook_ctrl_g() {
    local query="$READLINE_LINE"

    # Don't propose on empty line buffer
    if [[ -z "${query// }" ]]; then
        return
    fi

    # Execute nlctl propose with --emit-shell:
    # Stderr (risk tier and preview) prints directly to the terminal screen.
    # Stdout captures the eval commit command wrapper.
    local eval_cmd
    eval_cmd=$(nlctl propose "$query" --emit-shell 2>/dev/stderr)
    local ret_code=$?

    if [[ $ret_code -eq 0 && -n "$eval_cmd" ]]; then
        # Replace the current line buffer with the commit evaluation wrapper
        READLINE_LINE="$eval_cmd"
        READLINE_POINT=${#READLINE_LINE}
    fi
}

# Bind Ctrl+G to the hook function in interactive Bash sessions
if [[ $- == *i* ]]; then
    bind -x '"\C-g": nl_hook_ctrl_g'
fi


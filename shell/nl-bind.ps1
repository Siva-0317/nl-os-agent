# ==============================================================================
# nl-bind.ps1 - Native Terminal Keybinding (Ctrl+G) for PowerShell
#
# Usage:
#   Add the following line to your PowerShell profile ($PROFILE):
#     . "D:\Final yr Proj\shell\nl-bind.ps1"
#
# When you type a natural language instruction and press Ctrl+G:
# 1. Inspects the current PSReadLine console buffer.
# 2. Calls `nlctl propose "<buffer>" --emit-shell`.
# 3. Displays colored risk tier and predicted effect preview on stderr.
# 4. Replaces buffer with `nlctl commit <id> --emit-shell` for safe user review & execution.
# ==============================================================================

# Define global nlctl wrapper function pointing directly to main.py
function global:nlctl {
    python "D:\Final yr Proj\src\nlagent\cli\main.py" @args
}

# Define global eval helper for PowerShell so bash-style eval wrappers execute cleanly
function global:eval {
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$cmdArgs)
    $cmdStr = $cmdArgs -join " "
    if (-not [string]::IsNullOrWhiteSpace($cmdStr)) {
        Invoke-Expression $cmdStr
    }
}

if (Get-Module -Name PSReadLine) {
    $nlProposeAction = {
        $line = $null
        $cursor = $null
        [Microsoft.PowerShell.PSConsoleReadLine]::GetBufferState([ref]$line, [ref]$cursor)

        if ([string]::IsNullOrWhiteSpace($line)) {
            return
        }

        # Run nlctl propose with --emit-shell
        # Stderr (colored preview) renders directly; stdout returns commit command
        $evalCmd = & nlctl propose "$line" --emit-shell 2>&1 | Where-Object { $_ -is [string] }

        # Extract stdout string
        $proposedAction = ($evalCmd | Out-String).Trim()

        if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($proposedAction)) {
            # Convert bash-style eval "$(nlctl commit <id> --emit-shell)" to native PowerShell "nlctl commit <id>"
            if ($proposedAction -match 'nlctl commit (p_[a-zA-Z0-9_]+)') {
                $propId = $Matches[1]
                $proposedAction = "nlctl commit $propId"
            }
            [Microsoft.PowerShell.PSConsoleReadLine]::Replace(0, $line.Length, $proposedAction)
        }
    }

    # Bind Ctrl+G (for standalone PowerShell / Windows Terminal)
    Set-PSReadLineKeyHandler -Key "Ctrl+g" -BriefDescription "NL OS Agent Propose" -ScriptBlock $nlProposeAction

    # Bind Alt+G (for VS Code integrated terminal, avoiding VS Code's Ctrl+G conflict)
    Set-PSReadLineKeyHandler -Key "Alt+g" -BriefDescription "NL OS Agent Propose (VS Code friendly)" -ScriptBlock $nlProposeAction
}


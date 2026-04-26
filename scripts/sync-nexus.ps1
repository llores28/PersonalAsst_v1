# Sync the vendored Nexus toolkit (bootstrap/cli/) against upstream main.
#
# Why this exists:
#   The upstream one-liner `irm https://raw.githubusercontent.com/llores28/Nexus/main/setup.ps1 | iex`
#   is blocked by Windows Defender AMSI on the `irm | iex` cradle pattern.
#   Even if it weren't blocked, upstream's setup.ps1 would pip-install
#   nexus-bootstrap (upstream's `nexus/cli/` layout) and run `nexus init`,
#   which conflicts with this project's vendored `bootstrap/cli/` layout
#   and existing AI-OS files (CLAUDE.md, .windsurf, etc.).
#
# What this does instead — entirely from disk, no `iex`, no network cradle:
#   1. git pull the upstream-tracking checkout at bootstrap/Nexus/.
#   2. Diff bootstrap/Nexus/nexus/cli/ against bootstrap/cli/.
#   3. Report:
#        - new tool files upstream has that we don't (need porting)
#        - existing tool files whose logic actually changed
#          (vs. files that only differ in `nexus.cli.*` import paths)
#        - local-only tools we cherry-picked (preserved)
#   4. By default this is read-only. Pass -Apply to perform the safe ports
#      (new tools only, with import paths and hook templates rewritten for
#      the bootstrap/cli/ layout). Substantive edits to existing tools are
#      always left for human review.
#
# Usage:
#   .\scripts\sync-nexus.ps1                # report-only, no changes
#   .\scripts\sync-nexus.ps1 -Apply         # also port new tool files
#   .\scripts\sync-nexus.ps1 -SkipPull      # don't git pull first
#
# Excluded from porting:
#   - init.py, wizard.py — upstream "fresh project bootstrap" tools that
#     don't make sense on an already-bootstrapped project.

[CmdletBinding()]
param(
    [switch]$Apply,
    [switch]$SkipPull
)

$ErrorActionPreference = "Stop"

function Info($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "!!  $msg" -ForegroundColor Yellow }
function Err ($msg) { Write-Host "XX  $msg" -ForegroundColor Red }

$RepoRoot      = (Resolve-Path "$PSScriptRoot\..").Path
$UpstreamClone = Join-Path $RepoRoot "bootstrap\Nexus"
$UpstreamTools = Join-Path $UpstreamClone "nexus\cli\tools"
$LocalTools    = Join-Path $RepoRoot "bootstrap\cli\tools"

# Tools we deliberately do NOT port — fresh-bootstrap helpers.
$ExcludeTools = @("init.py", "wizard.py", "__init__.py")

if (-not (Test-Path $UpstreamClone)) {
    Err "Upstream clone not found at $UpstreamClone"
    Write-Host "    Initialize it first:"
    Write-Host "      git clone https://github.com/llores28/Nexus.git bootstrap\Nexus"
    exit 1
}

# --- 1. Pull upstream ---
if (-not $SkipPull) {
    Info "Fetching upstream main into bootstrap\Nexus"
    Push-Location $UpstreamClone
    try {
        $beforeSha = (& git rev-parse HEAD).Trim()
        & git fetch origin 2>&1 | Out-Null
        & git checkout main 2>&1 | Out-Null
        & git pull --ff-only origin main 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Err "git pull --ff-only failed. Resolve manually in $UpstreamClone."
            exit 1
        }
        $afterSha = (& git rev-parse HEAD).Trim()
        if ($beforeSha -eq $afterSha) {
            Write-Host "    Already at $afterSha"
        } else {
            Write-Host "    $beforeSha -> $afterSha"
        }
    } finally {
        Pop-Location
    }
} else {
    Info "Skipping pull (-SkipPull)"
}

if (-not (Test-Path $UpstreamTools)) {
    Err "Upstream tools directory not found at $UpstreamTools"
    Write-Host "    Has upstream renamed the layout again? Inspect bootstrap\Nexus\."
    exit 1
}

# --- 2. Categorize tool files ---
$upstreamFiles = Get-ChildItem $UpstreamTools -Filter *.py | ForEach-Object { $_.Name }
$localFiles    = Get-ChildItem $LocalTools    -Filter *.py | ForEach-Object { $_.Name }

$newUpstream     = @()  # in upstream, missing locally  → candidates to port
$bothExist       = @()  # in both                       → candidates for refresh
$localOnly       = @()  # in local, not upstream        → cherry-picks to preserve
$excluded        = @()  # explicitly skipped

foreach ($f in $upstreamFiles) {
    if ($ExcludeTools -contains $f) {
        $excluded += $f
    } elseif ($localFiles -contains $f) {
        $bothExist += $f
    } else {
        $newUpstream += $f
    }
}
foreach ($f in $localFiles) {
    if (-not ($upstreamFiles -contains $f) -and -not ($ExcludeTools -contains $f)) {
        $localOnly += $f
    }
}

# --- 3. For files in both, decide whether the diff is import-only or substantive ---
function Test-ImportOnlyDiff {
    param([string]$UpstreamPath, [string]$LocalPath)
    # Returns $true if the only differences are bootstrap.cli.* <-> nexus.cli.*
    # (and bootstrap/cli/ <-> nexus/cli/ in path strings inside the file).
    $upRaw = (Get-Content $UpstreamPath -Raw) -replace 'nexus\.cli\.', 'bootstrap.cli.' `
                                              -replace 'nexus/cli/', 'bootstrap/cli/' `
                                              -replace '_NEXUS_DIR', '_BOOTSTRAP_DIR' `
                                              -replace 'nexus package is importable', 'bootstrap package is importable'
    $loRaw = Get-Content $LocalPath -Raw
    return ($upRaw -ceq $loRaw)
}

$importOnly = @()
$substantive = @()
foreach ($f in $bothExist) {
    $up = Join-Path $UpstreamTools $f
    $lo = Join-Path $LocalTools $f
    if (Test-ImportOnlyDiff -UpstreamPath $up -LocalPath $lo) {
        $importOnly += $f
    } else {
        $substantive += $f
    }
}

# --- 4. Report ---
Write-Host ""
Info "Sync report — bootstrap/cli/tools/"
Write-Host ""
Write-Host "  Excluded (fresh-bootstrap only, never ported):"
foreach ($f in $excluded) { Write-Host "    - $f" -ForegroundColor DarkGray }

Write-Host ""
Write-Host "  Local-only (cherry-picks, preserved):" -ForegroundColor DarkGray
if ($localOnly.Count -eq 0) { Write-Host "    (none)" -ForegroundColor DarkGray }
foreach ($f in $localOnly) { Write-Host "    - $f" -ForegroundColor DarkGray }

Write-Host ""
Write-Host "  Identical apart from import paths (no action needed):" -ForegroundColor Green
if ($importOnly.Count -eq 0) { Write-Host "    (none)" -ForegroundColor Green }
foreach ($f in $importOnly) { Write-Host "    - $f" -ForegroundColor Green }

Write-Host ""
Write-Host "  Substantive changes upstream (review manually):" -ForegroundColor Yellow
if ($substantive.Count -eq 0) { Write-Host "    (none)" -ForegroundColor Yellow }
foreach ($f in $substantive) {
    $up = Join-Path $UpstreamTools $f
    $lo = Join-Path $LocalTools $f
    $upLines = (Get-Content $up).Count
    $loLines = (Get-Content $lo).Count
    Write-Host ("    - {0}  (upstream {1} lines, local {2} lines)" -f $f, $upLines, $loLines) -ForegroundColor Yellow
}

Write-Host ""
Write-Host "  New upstream tools (need porting):" -ForegroundColor Magenta
if ($newUpstream.Count -eq 0) { Write-Host "    (none)" -ForegroundColor Magenta }
foreach ($f in $newUpstream) { Write-Host "    - $f" -ForegroundColor Magenta }

# --- 5. Optional apply: port new tools with rewrites ---
if (-not $Apply) {
    Write-Host ""
    Info "Report-only. Pass -Apply to port new tools (rewriting imports + hook paths)."
    Write-Host "    Substantive existing-file changes are never auto-applied; review and edit by hand."
    exit 0
}

if ($newUpstream.Count -eq 0 -and $substantive.Count -eq 0) {
    Write-Host ""
    Info "Nothing to apply."
    exit 0
}

Write-Host ""
Info "Applying ports for new tools"
foreach ($f in $newUpstream) {
    $src = Join-Path $UpstreamTools $f
    $dst = Join-Path $LocalTools    $f
    $body = Get-Content $src -Raw

    # Rewrite upstream imports + path constants for the bootstrap/cli/ layout.
    $body = $body -replace 'from nexus\.cli\.', 'from bootstrap.cli.'
    $body = $body -replace 'import nexus\.cli\.', 'import bootstrap.cli.'
    # Inside git-hook templates: paths to bs_cli.py.
    $body = $body -replace '\$NEXUS_ROOT/nexus/cli/bs_cli\.py', '$NEXUS_ROOT/bootstrap/cli/bs_cli.py'
    # Header comments referencing the upstream layout.
    $body = $body -replace 'nexus/cli/bs_cli\.py', 'bootstrap/cli/bs_cli.py'

    Set-Content -Path $dst -Value $body -Encoding UTF8 -NoNewline
    Write-Host "    ported: $f -> bootstrap\cli\tools\$f" -ForegroundColor Magenta
}

Write-Host ""
Write-Host "  Reminder: register any new subcommands in bootstrap\cli\bs_cli.py" -ForegroundColor Yellow
Write-Host "  and bump the version. Substantive existing-file changes still need manual review:" -ForegroundColor Yellow
foreach ($f in $substantive) { Write-Host "    - $f" -ForegroundColor Yellow }
Write-Host ""
Info "Done."

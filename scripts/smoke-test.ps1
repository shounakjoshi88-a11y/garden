# Builds a throwaway sandbox and exercises every garden command end to end.
# Usage:  pwsh -File scripts/smoke-test.ps1
param(
    [string]$Root = "$env:TEMP\opencode\garden-smoke"
)

$ErrorActionPreference = 'Stop'
$GardenRoot = Split-Path -Parent $PSScriptRoot
Set-Location $GardenRoot

if (Test-Path $Root) { Remove-Item -Recurse -Force $Root }
New-Item -ItemType Directory -Path $Root | Out-Null

function New-DemoRepo([string]$Path, [string]$Name) {
    New-Item -ItemType Directory -Path "$Path\src\api" -Force | Out-Null
    New-Item -ItemType Directory -Path "$Path\tests" -Force | Out-Null
    New-Item -ItemType Directory -Path "$Path\docs" -Force | Out-Null
    Set-Content "$Path\src\api\users.py" "def get_user():`n    return 1`n`n# TODO: pagination`n"
    Set-Content "$Path\src\api\auth.py"  "def login():`n    pass`n"
    Set-Content "$Path\src\main.py"      "def main():`n    print('hi')`n"
    Set-Content "$Path\tests\test_users.py" "def test_get_user():`n    assert True`n"
    Set-Content "$Path\docs\readme.md"   "# Docs`n"
    Set-Content "$Path\package.json"     '{ "name":"demo","scripts":{"test":"echo no tests"} }'
    git -C $Path init -b main -q
    git -C $Path config user.name "demo"
    git -C $Path config user.email "demo@example.dev"
    git -C $Path add -A
    git -C $Path commit -qm "initial commit"
}

Write-Host "== building sandbox at $Root"
$proj = Join-Path $Root 'proj'
New-DemoRepo $proj 'proj'

# a messy batch of work
Set-Content "$proj\src\api\users.py" "def get_user():`n    return 2`n`ndef delete_user(uid):`n    return uid`n`n# TODO: pagination`n"
Set-Content "$proj\src\api\auth.py"  "def login():`n    return 'token'`n`n# FIXME: expire tokens`n"
Set-Content "$proj\tests\test_users.py" "def test_get_user():`n    assert True`n`ndef test_delete_user():`n    assert True`n"
Set-Content "$proj\docs\readme.md" "# Docs`n`nUpdated.`n"

$cfg = @{
    version = 1
    ops_repo = @{ path = (Join-Path $Root 'ops') }
    tasks = @('metrics', 'todos', 'notes')
    max_commits_per_day = 6
    commit_hour = 12
    topics = @('systems thinking', 'testing', 'performance')
    sources = @()
    notes_dir = 'notes'
    fleet = @(
        @{ name = 'proj'; path = $proj; tasks = @('metrics', 'todos', 'notes'); enabled = $true; write_ops_here = $true },
        @{ name = 'mirror'; path = $proj; tasks = @('metrics', 'todos'); enabled = $true; write_ops_here = $false }
    )
} | ConvertTo-Json -Depth 6
$configPath = Join-Path $Root 'garden.config.json'
Set-Content $configPath $cfg -Encoding utf8

Write-Host "`n== tasks"
python -m garden tasks
if ($LASTEXITCODE -ne 0) { throw "tasks failed" }

Write-Host "`n== split (dry run)"
python -m garden split $proj --dry-run
if ($LASTEXITCODE -ne 0) { throw "split dry run failed" }

Write-Host "`n== split (apply)"
python -m garden split $proj
if ($LASTEXITCODE -ne 0) { throw "split failed" }
$splitCount = (git -C $proj rev-list --count HEAD)
Write-Host "   commits in demo repo now: $splitCount"

Write-Host "`n== day (dry run)"
python -m garden --config $configPath day --dry-run
if ($LASTEXITCODE -ne 0) { throw "day dry run failed" }

Write-Host "`n== day (real)"
python -m garden --config $configPath day
if ($LASTEXITCODE -ne 0) { throw "day failed" }

Write-Host "`n== backfill (7 days)"
python -m garden --config $configPath backfill --from 2026-10-14 --to 2026-10-20 --tasks metrics --cap 2
if ($LASTEXITCODE -ne 0) { throw "backfill failed" }

Write-Host "`n== status"
python -m garden --config $configPath status --weeks 4
if ($LASTEXITCODE -ne 0) { throw "status failed" }

Write-Host "`n== ops repo history"
git -C (Join-Path $Root 'ops') log --format='%h %ad %s' --date=short

Write-Host "`nSMOKE OK"

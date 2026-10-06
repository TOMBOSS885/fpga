param(
    [ValidateSet('public','regression')][string]$Suite='public',
    [string]$Model='qwen2.5-coder:7b-instruct-q4_K_M',
    [string]$BaseUrl='http://127.0.0.1:11435/v1',
    [string]$VivadoBin='D:\AMDDesignTools\2026.1\Vivado\bin',
    [string]$Python='python',
    [string]$Output='',
    [switch]$NoSkills,
    [switch]$FixtureHealth
)
$ErrorActionPreference='Stop'
$project=Split-Path -Parent $PSScriptRoot
if(-not $Output){ $Output=Join-Path $project ('experiments\'+$Suite+'_'+(Get-Date -Format 'yyyyMMdd_HHmmss')) }
$env:PYTHONUTF8='1'
$env:VIVADO_BIN=$VivadoBin
$env:XILINX_LOCAL_USER_DATA='NO'
$env:LLM_BASE_URL=$BaseUrl
$env:LLM_MODEL=$Model
$env:NO_PROXY='localhost,127.0.0.1'
$env:AGENT_DEADLINE_S='150'
$env:AGENT_MAX_ROUNDS='2'
$env:LLM_TIMEOUT_S='45'
$parameters=@('-B',(Join-Path $project 'evaluation\run_evaluation.py'),'--suite',$Suite,'--output',$Output)
if($NoSkills){$parameters+=@('--mode','no_skills')}
if($FixtureHealth){$parameters+='--fixture-health'}
# Reuse an already running local model service; never download weights or
# install/start services implicitly. Output directory must be new.
Push-Location $project
try {
    & $Python @parameters
    if($LASTEXITCODE -ne 0){throw 'Development evaluation failed; inspect console output.'}
} finally {Pop-Location}

# BioAgentDB 一键启动（Docker / PowerShell）。用法:
#   .\start.ps1                 # 只起服务 + UI
#   .\start.ps1 -WithMining     # 同时起挖掘矿机（mining profile）
param(
    [switch]$WithMining
)
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "已从 .env.example 生成 .env —— 请填入 DEEPSEEK_API_KEY 后重新运行。"
}
New-Item -ItemType Directory -Force -Path "data" | Out-Null

$profileArgs = @()
if ($WithMining) { $profileArgs = @("--profile", "mining") }

docker compose @profileArgs up -d --build

Write-Host ""
Write-Host "BioAgentDB:  http://localhost:8000"
Write-Host "logs:        docker compose logs -f app"
if ($WithMining) { Write-Host "miner logs:  docker compose logs -f miner" }
Write-Host "stop:        docker compose down"

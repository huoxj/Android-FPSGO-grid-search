# scripts/build.ps1
$ErrorActionPreference = 'Stop'
Push-Location "$PSScriptRoot\.."

# 1. Download dependencies
# perfetto trace processor shell
$TpDir = 'vendor\perfetto'
$TpBin = "$TpDir\tp_shell_bin.exe"
$TpUrl = "https://commondatastorage.googleapis.com/perfetto-luci-artifacts/v57.2/windows-amd64/trace_processor_shell.exe"
$TpSha256 = '100334b6091596fbc97f872556849a5747bf47a7f7190c485ba8cea8d2409c7b'
New-Item -ItemType Directory -Force -Path $TpDir | Out-Null
if (-not (Test-Path $TpBin)) {
  try { Invoke-WebRequest -Uri $TpUrl -OutFile $TpBin -TimeoutSec 120 }
  catch { Write-Host "Download failed, try manually download to $TpBin`n  $TpUrl"; exit 1 }
}
$actual = (Get-FileHash $TpBin -Algorithm SHA256).Hash.ToLower()
if ($actual -ne $TpSha256) { Write-Host "sha256 checksum failed: $actual"; exit 1 }

# 2. Build executable
$Name = 'fpsgo-optim'
$Version = git describe --tags --always 2>$null
if (-not $Version) { $Version = 'dev' }
$Target = "windows-$($env:PROCESSOR_ARCHITECTURE.ToLower())"

Remove-Item -Recurse -Force dist, build, .pyarmor\pack, main.spec -ErrorAction SilentlyContinue
uv run pyarmor gen --pack onefile -O dist src/main.py

uv run pyinstaller --onefile --name $Name `
  --additional-hooks-dir .pyarmor\pack `
  --collect-data perfetto `
  .pyarmor\pack\dist\main.py

# 3. Create package
$Pkg = "dist\${Name}-${Version}-${Target}"
Remove-Item -Recurse -Force $Pkg -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $Pkg | Out-Null
Move-Item "dist\$Name.exe" "$Pkg\$Name.exe" -Force
Copy-Item -Recurse resources, perfetto_configs, docs -Destination $Pkg
Copy-Item config.example.toml "$Pkg\config.toml"
Copy-Item README.md, LICENSE $Pkg

New-Item -ItemType Directory -Force -Path "$Pkg\vendor\perfetto" | Out-Null
Copy-Item $TpBin "$Pkg\vendor\perfetto\tp_shell_bin.exe"

Compress-Archive -Path $Pkg -DestinationPath "dist\${Name}-${Version}-${Target}.zip" -Force
Write-Host "done: dist\${Name}-${Version}-${Target}.zip"

Pop-Location


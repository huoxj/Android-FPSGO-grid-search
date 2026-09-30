# scripts/build.ps1
$ErrorActionPreference = 'Stop'
Push-Location "$PSScriptRoot\.."

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

$Pkg = "dist\${Name}-${Version}-${Target}"
Remove-Item -Recurse -Force $Pkg -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $Pkg | Out-Null
Move-Item "dist\$Name.exe" "$Pkg\$Name.exe" -Force
Copy-Item -Recurse resources, perfetto_configs, docs -Destination $Pkg
Copy-Item config.example.toml "$Pkg\config.toml"
Copy-Item README.md, LICENSE $Pkg

Compress-Archive -Path $Pkg -DestinationPath "dist\${Name}-${Version}-${Target}.zip" -Force
Write-Host "done: dist\${Name}-${Version}-${Target}.zip"

Pop-Location


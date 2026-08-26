param(
    [string]$BuildDirectory = "build_stable_a1",
    [string]$DistDirectory = "dist",
    [string]$WorkDirectory = "build-pyinstaller"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$build = Join-Path $repo $BuildDirectory
$stage = Join-Path $repo "_package_stage"
$sdkconfigHeader = Join-Path $build "config\sdkconfig.h"

if (-not (Test-Path -LiteralPath $sdkconfigHeader -PathType Leaf)) {
    throw "Missing generated build configuration: $sdkconfigHeader"
}

$buildConfig = Get-Content -LiteralPath $sdkconfigHeader -Raw
function Require-BuildConfig([string]$Name) {
    if ($buildConfig -notmatch "(?m)^#define $([regex]::Escape($Name)) 1$") {
        throw "Refusing to package: $Name is not enabled in $sdkconfigHeader"
    }
}

# Never ship a receiver-console EXE whose Kconfig dependencies silently
# disabled the autonomous RF-to-CVBS path.
Require-BuildConfig "CONFIG_C5VRX_EXPERIMENTAL_RF_DUMP_PRODUCER"
Require-BuildConfig "CONFIG_C5VRX_EXPERIMENTAL_CVBS_PARLIO"
Require-BuildConfig "CONFIG_C5VRX_AUTO_A1_AV"

New-Item -ItemType Directory -Force -Path $stage | Out-Null
Copy-Item -LiteralPath (Join-Path $build "bootloader\bootloader.bin") `
    -Destination (Join-Path $stage "bootloader.bin") -Force
Copy-Item -LiteralPath (Join-Path $build "partition_table\partition-table.bin") `
    -Destination (Join-Path $stage "partition-table.bin") -Force
Copy-Item -LiteralPath (Join-Path $build "C5VRX.bin") `
    -Destination (Join-Path $stage "C5VRX.bin") -Force
Copy-Item -LiteralPath (Join-Path $build "flasher_args.json") `
    -Destination (Join-Path $stage "flasher_args.json") -Force
Copy-Item -LiteralPath (Join-Path $repo "firmware_profiles\xiao-esp32c5.json") `
    -Destination (Join-Path $stage "profile.json") -Force

Push-Location $repo
try {
    python -m PyInstaller --noconfirm --clean `
        --distpath (Join-Path $repo $DistDirectory) `
        --workpath (Join-Path $repo $WorkDirectory) `
        "C5VRX-XIAO-A1-Fixed-Receiver-Console.spec"
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
    Get-FileHash `
        (Join-Path (Join-Path $repo $DistDirectory) "C5VRX-XIAO-A1-Fixed-Receiver-Console.exe") `
        -Algorithm SHA256
}
finally {
    Pop-Location
}

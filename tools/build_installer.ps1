param(
    [string]$Python = 'python',
    [string]$Iscc = 'ISCC.exe'
)

$ErrorActionPreference = 'Stop'
$compiler = (Get-Command $Iscc -CommandType Application).Source
Push-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
try {
    $platform = & $Python -c 'import sysconfig; print(sysconfig.get_platform())'
    if ($LASTEXITCODE -ne 0 -or $platform -ne 'win-amd64') {
        throw 'The x64 installer requires a Windows x64 Python interpreter.'
    }
    $version = & $Python -c 'import codepoint; print(codepoint.__version__)'
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not read the application version.'
    }
    & $Python -m PyInstaller --noconfirm --distpath dist/fast CodePoint.spec -- --onedir
    if ($LASTEXITCODE -ne 0) {
        throw 'PyInstaller build failed.'
    }
    & $compiler /Qp "/DAppVersion=$version" 'build/CodePoint.iss'
    if ($LASTEXITCODE -ne 0) {
        throw 'Inno Setup build failed.'
    }
    Get-Item -LiteralPath "dist/CodePoint-$version-Setup-x64.exe" | Select-Object FullName, Length
} finally {
    Pop-Location
}

Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
python -m pip install pyinstaller pywebview
$icon = "icons\app.ico"
$args = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--windowed",
    "--name", "datamining-pdd",
    "--add-data", "web;web",
    "--add-data", "icons;icons",
    "--add-data", "pdd_search.py;.",
    "app.py"
)
if (Test-Path -LiteralPath $icon) {
    $args += @("--icon", $icon)
}
python @args
Write-Host "生成文件：dist\datamining-pdd\datamining-pdd.exe"

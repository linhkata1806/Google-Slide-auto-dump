# Run with: powershell.exe -NoProfile -File tests/test_capture_functions.ps1
$ErrorActionPreference = 'Stop'
$source = Join-Path (Split-Path $PSScriptRoot -Parent) 'screenshot-recorder.ps1'
$raw = Get-Content -LiteralPath $source -Raw
$startup = $raw.Substring(0, $raw.IndexOf('# Load SendKey type only once'))
Invoke-Expression $startup

$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($source, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw "PowerShell syntax errors: $errors" }
$functionNames = @('Capture-Screen', 'Select-ScreenRegion', 'Crop-Image', 'Merge-SlideAndNotes',
    'Test-CaptureRegion', 'Get-CaptureDpi', 'Load-CaptureConfig', 'Save-CaptureConfig')
$functions = $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)
foreach ($name in $functionNames) {
    $function = $functions | Where-Object Name -eq $name | Select-Object -First 1
    if ($null -eq $function) { throw "Missing function: $name" }
    Invoke-Expression $function.Extent.Text
}

function Assert($condition, $message) { if (-not $condition) { throw $message } }
$screen = [System.Drawing.Rectangle]::new(0, 0, 1920, 1080)
$slide = [System.Drawing.Rectangle]::new(100, 50, 1280, 720)
$notes = [System.Drawing.Rectangle]::new(100, 790, 1280, 250)
Assert (Test-CaptureRegion $slide $screen) 'valid slide rejected'
Assert (Test-CaptureRegion $notes $screen) 'valid notes rejected'
Assert (-not (Test-CaptureRegion ([System.Drawing.Rectangle]::new(1900, 10, 50, 10)) $screen)) 'out-of-bounds accepted'
Assert (-not (Test-CaptureRegion ([System.Drawing.Rectangle]::new(0, 0, 0, 10)) $screen)) 'zero width accepted'
$offsetScreen = [System.Drawing.Rectangle]::new(-1920, 0, 1920, 1080)
$offsetRegion = [System.Drawing.Rectangle]::new(-1820, 50, 100, 100)
Assert (Test-CaptureRegion $offsetRegion $offsetScreen) 'negative screen origin rejected'

$bitmap = [System.Drawing.Bitmap]::new(1920, 1080)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.Clear([System.Drawing.Color]::Blue)
$graphics.Dispose()
$slideImage = Crop-Image $bitmap $slide $screen
$notesImage = Crop-Image $bitmap $notes $screen
$offsetCrop = Crop-Image $bitmap $offsetRegion $offsetScreen
$merged = Merge-SlideAndNotes $slideImage $notesImage
try {
    Assert ($offsetCrop.Width -eq 100 -and $offsetCrop.Height -eq 100) 'negative screen origin cropped incorrectly'
    Assert ($slideImage.Width -eq 1280 -and $slideImage.Height -eq 720) 'slide crop dimensions changed'
    Assert ($notesImage.Width -eq 1280 -and $notesImage.Height -eq 250) 'notes crop dimensions changed'
    Assert ($merged.Width -gt $slideImage.Width -and $merged.Height -gt ($slideImage.Height + $notesImage.Height)) 'padding or gap missing'
    Assert ($merged.GetPixel(20, 20).ToArgb() -eq [System.Drawing.Color]::Blue.ToArgb()) 'slide pixels changed'
    $gapY = $slideImage.Height + 20
    Assert ($merged.GetPixel(20, $gapY).ToArgb() -eq [System.Drawing.Color]::White.ToArgb()) 'gap is not white'
} finally {
    $merged.Dispose(); $slideImage.Dispose(); $notesImage.Dispose(); $offsetCrop.Dispose(); $bitmap.Dispose()
}

$script:testDpi = 120
function Get-CaptureDpi { return $script:testDpi }
$path = Join-Path $env:TEMP ('capture-config-test-' + [Guid]::NewGuid().ToString() + '.json')
try {
    foreach ($dpi in @(120, 144, 192)) {
        $script:testDpi = $dpi
        Save-CaptureConfig $path $screen $slide $notes
        $loaded = Load-CaptureConfig $path $screen
        Assert ($null -ne $loaded) "saved config did not load at DPI $dpi"
        Assert ($loaded.slide.width -eq 1280) 'slide width not persisted'
    }
    $script:testDpi = 120
    Assert ($null -eq (Load-CaptureConfig $path $screen)) 'old DPI config accepted'
    $script:testDpi = 192
    $config = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
    $config.notes.width = 3000
    $config | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $path
    Assert ($null -eq (Load-CaptureConfig $path $screen)) 'invalid config accepted'
    Set-Content -LiteralPath $path -Value '{invalid-json'
    Assert ($null -eq (Load-CaptureConfig $path $screen)) 'malformed JSON accepted'
} finally { Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue }
Write-Host 'Capture function tests passed.'

# Run with: powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests/test_browser_capture.ps1
$ErrorActionPreference = 'Stop'
$source = Join-Path (Split-Path $PSScriptRoot -Parent) 'screenshot-recorder.ps1'
$raw = Get-Content -LiteralPath $source -Raw
Invoke-Expression $raw.Substring(0, $raw.IndexOf('# Load SendKey type only once'))
Invoke-Expression $raw.Substring($raw.IndexOf('# Load SendKey type only once'),
    $raw.IndexOf('# ----- Source and parameters -----') - $raw.IndexOf('# Load SendKey type only once'))

$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($source, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw "PowerShell syntax errors: $errors" }
$names = @('Test-CaptureRegion', 'Validate-CaptureArea', 'Crop-Image', 'Merge-SlideAndNotes',
    'Save-CaptureConfig', 'Save-BrowserCaptureConfig', 'Load-BrowserCaptureConfig', 'Load-SavedRegionsForBrowser', 'Test-SameRectangle',
    'Capture-CurrentSlideWithNotes', 'Test-SlideFrameChanged', 'Go-ToFirstSlide',
    'Go-ToNextSlide')
$functions = $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)
foreach ($name in $names) {
    $function = $functions | Where-Object Name -eq $name | Select-Object -First 1
    if ($null -eq $function) { throw "Missing function: $name" }
    Invoke-Expression $function.Extent.Text
}
function Assert($condition, $message) { if (-not $condition) { throw $message } }

# Simulate a secondary monitor with a negative physical-pixel origin.
$desktop = [System.Drawing.Rectangle]::new(-800, 0, 1200, 400)
$window = [System.Drawing.Rectangle]::new(-760, 20, 700, 350)
$slide = [System.Drawing.Rectangle]::new(-520, 90, 430, 180)
$notes = [System.Drawing.Rectangle]::new(-520, 280, 430, 65)
$selectorBitmap = [System.Drawing.Bitmap]::new(1200, 400)
$selector = [CaptureRegionSelector]::new($selectorBitmap, $desktop, 'Slide')
try {
    $flags = [System.Reflection.BindingFlags]::Instance -bor [System.Reflection.BindingFlags]::NonPublic
    $down = $selector.GetType().GetMethod('OnMouseDown', $flags)
    $up = $selector.GetType().GetMethod('OnMouseUp', $flags)
    $down.Invoke($selector, @([System.Windows.Forms.MouseEventArgs]::new([System.Windows.Forms.MouseButtons]::Left, 1, 100, 80, 0))) | Out-Null
    $up.Invoke($selector, @([System.Windows.Forms.MouseEventArgs]::new([System.Windows.Forms.MouseButtons]::Left, 1, 300, 180, 0))) | Out-Null
    Assert ($selector.SelectedRegion.X -eq -700 -and $selector.SelectedRegion.Y -eq 80 -and
        $selector.SelectedRegion.Width -eq 200 -and $selector.SelectedRegion.Height -eq 100) 'mouse selection mapped to wrong monitor coordinates'
} finally { $selector.Dispose(); $selectorBitmap.Dispose() }
$path = Join-Path $env:TEMP ('browser-config-test-' + [Guid]::NewGuid().ToString() + '.json')
try {
    # Preserve the existing Presenter View config when writing browser settings.
    @{ version = 1; screen = @{ x = 0; y = 0; width = 1920; height = 1080; dpi = 144 };
       slide = @{ x = 100; y = 100; width = 800; height = 450 };
       notes = @{ x = 100; y = 600; width = 800; height = 200 } } |
        ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $path
    $oldWindow = [System.Drawing.Rectangle]::new(0, 0, 1920, 1000)
    $oldRegions = Load-SavedRegionsForBrowser $path $oldWindow 144
    Assert ($null -ne $oldRegions -and $oldRegions.notes.height -eq 200) 'existing Slide/Notes coordinates could not be reused'
    Assert ($null -eq (Load-SavedRegionsForBrowser $path $oldWindow 120)) 'existing coordinates reused at wrong DPI'
    foreach ($dpi in @(96, 120, 144, 192)) {
        Save-BrowserCaptureConfig $path $desktop $window $dpi 'slide-notes' $slide $notes
        $loaded = Load-BrowserCaptureConfig $path $desktop $window $dpi 'slide-notes'
        Assert ($null -ne $loaded) "browser config failed at DPI $dpi"
        Assert ($loaded.slide.width -eq 430 -and $loaded.notes.height -eq 65) 'browser regions changed'
    }
    $empty = [System.Drawing.Rectangle]::Empty
    Save-BrowserCaptureConfig $path $desktop $window 192 'full-window' $empty $empty
    Assert ($null -ne (Load-BrowserCaptureConfig $path $desktop $window 192 'full-window')) 'full-window config failed'
    $oneArea = [System.Drawing.Rectangle]::new(-520, 90, 430, 255)
    Save-BrowserCaptureConfig $path $desktop $window 192 'single-region' $empty $empty $oneArea
    $singleConfig = Load-BrowserCaptureConfig $path $desktop $window 192 'single-region'
    Assert ($singleConfig.area.width -eq 430 -and $singleConfig.area.height -eq 255) 'single-region config failed'
    Save-BrowserCaptureConfig $path $desktop $window 192 'slide-notes' $slide $notes
    $all = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
    Assert ($all.screen.width -eq 1920) 'Presenter View config was lost'
    function Get-CaptureDpi { return 144 }
    $presenterScreen = [System.Drawing.Rectangle]::new(0, 0, 1920, 1080)
    $presenterSlide = [System.Drawing.Rectangle]::new(100, 100, 800, 450)
    $presenterNotes = [System.Drawing.Rectangle]::new(100, 600, 800, 200)
    Save-CaptureConfig $path $presenterScreen $presenterSlide $presenterNotes
    Assert ($null -ne (Load-BrowserCaptureConfig $path $desktop $window 192 'slide-notes')) 'browser config lost after Presenter View save'
    Assert ($null -eq (Load-BrowserCaptureConfig $path $desktop $window 120 'slide-notes')) 'stale DPI config accepted'
    Assert ($null -eq (Load-BrowserCaptureConfig $path ([System.Drawing.Rectangle]::new(-800, 0, 1300, 400)) $window 192 'slide-notes')) 'changed desktop accepted'
    $all.browser.notes.width = 1000
    $all | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $path
    Assert ($null -eq (Load-BrowserCaptureConfig $path $desktop $window 192 'slide-notes')) 'out-of-window notes accepted'
} finally { Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue }

$frame = [System.Drawing.Bitmap]::new(1200, 400)
$graphics = [System.Drawing.Graphics]::FromImage($frame)
$graphics.Clear([System.Drawing.Color]::White)
$graphics.FillRectangle([System.Drawing.Brushes]::Blue, 280, 90, 430, 180)
$graphics.FillRectangle([System.Drawing.Brushes]::Green, 280, 280, 430, 65)
$graphics.Dispose()
try {
    $whole = Capture-CurrentSlideWithNotes $frame $desktop $window 'full-window' $slide $notes
    $joined = Capture-CurrentSlideWithNotes $frame $desktop $window 'slide-notes' $slide $notes
    $single = Capture-CurrentSlideWithNotes $frame $desktop $window 'single-region' $slide $notes $oneArea
    try {
        Assert ($whole.Width -eq 700 -and $whole.Height -eq 350) 'full-window dimensions changed'
        Assert ($joined.Width -gt 430 -and $joined.Height -gt 245) 'slide + notes composition failed'
        Assert ($single.Width -eq 430 -and $single.Height -eq 255) 'one-area crop dimensions changed'
        Assert ($single.GetPixel(10, 10).ToArgb() -eq [System.Drawing.Color]::Blue.ToArgb()) 'slide missing from one-area crop'
        Assert ($single.GetPixel(10, 200).ToArgb() -eq [System.Drawing.Color]::Green.ToArgb()) 'notes missing from one-area crop'
    } finally { $whole.Dispose(); $joined.Dispose(); $single.Dispose() }

    $changed = [System.Drawing.Bitmap]::new(1200, 400)
    $onlyCanvas = [System.Drawing.Bitmap]::new(1200, 400)
    $g = [System.Drawing.Graphics]::FromImage($changed)
    $g.DrawImageUnscaled($frame, 0, 0)
    # A selected-thumbnail border and changed slide canvas.
    $g.FillRectangle([System.Drawing.Brushes]::Red, 50, 160, 100, 20)
    $g.FillRectangle([System.Drawing.Brushes]::Red, 300, 120, 100, 20)
    $g.Dispose()
    $g = [System.Drawing.Graphics]::FromImage($onlyCanvas)
    $g.DrawImageUnscaled($frame, 0, 0)
    $g.FillRectangle([System.Drawing.Brushes]::Red, 300, 120, 100, 20)
    $g.Dispose()
    try {
        Assert (-not (Test-SlideFrameChanged $frame $frame $window $desktop)) 'same slide counted as changed'
        Assert (-not (Test-SlideFrameChanged $frame $onlyCanvas $window $desktop)) 'canvas-only change counted as filmstrip navigation'
        Assert (Test-SlideFrameChanged $frame $changed $window $desktop) 'filmstrip + slide change not detected'

        $script:keyCount = 0; $script:shotCount = 0
        function Focus-GoogleSlidesFilmstrip([IntPtr]$handle) { }
        function Send-KeyChord([byte[]]$keys) { $script:keyCount++ }
        function Start-Sleep { param([int]$Milliseconds) }
        function Get-WindowRectangle([IntPtr]$handle) { return $window }
        function Take-Screenshot([System.Drawing.Rectangle]$bounds) {
            $script:shotCount++
            if ($script:shotCount -le 2) { return [System.Drawing.Bitmap]$frame.Clone() }
            return [System.Drawing.Bitmap]$changed.Clone()
        }
        $next = Go-ToNextSlide ([IntPtr]::new(1)) $frame $desktop $window 1
        try {
            Assert ($script:keyCount -eq 2) 'navigation did not retry once after unchanged frames'
            Assert ($script:shotCount -eq 3) 'navigation screenshot sequence is wrong'
        } finally { $next.Dispose() }
        Go-ToFirstSlide ([IntPtr]::new(1)) 1
        Assert ($script:keyCount -eq 3) 'first-slide shortcut was not sent'
    } finally { $changed.Dispose(); $onlyCanvas.Dispose() }
} finally { $frame.Dispose() }
Write-Host 'Browser capture tests passed.'

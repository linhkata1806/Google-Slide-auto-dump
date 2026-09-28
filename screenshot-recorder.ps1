# Region selection and screen capture use physical pixels at any Windows display scale.
# Set DPI awareness before Windows Forms queries screen coordinates.
if (-not ("CaptureDpi" -as [type])) {
    Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class CaptureDpi {
    [DllImport("user32.dll")] static extern bool SetProcessDpiAwarenessContext(IntPtr value);
    [DllImport("user32.dll")] static extern IntPtr SetThreadDpiAwarenessContext(IntPtr value);
    [DllImport("shcore.dll")] static extern int SetProcessDpiAwareness(int value);
    [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
    public static void Enable() {
        try { if (SetProcessDpiAwarenessContext(new IntPtr(-4))) return; }
        catch (DllNotFoundException) {} catch (EntryPointNotFoundException) {}
        try { if (SetThreadDpiAwarenessContext(new IntPtr(-4)) != IntPtr.Zero) return; }
        catch (DllNotFoundException) {} catch (EntryPointNotFoundException) {}
        try { if (SetProcessDpiAwareness(2) == 0) return; }
        catch (DllNotFoundException) {} catch (EntryPointNotFoundException) {}
        SetProcessDPIAware();
    }
}
"@
}
[CaptureDpi]::Enable()
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms

if (-not ("CaptureRegionSelector" -as [type])) {
    $selectorAssemblies = if ($PSVersionTable.PSEdition -eq 'Desktop') {
        @('System.Drawing', 'System.Windows.Forms')
    } else {
        [System.Reflection.Assembly]::Load('System.Private.Windows.Core') | Out-Null
        @([AppDomain]::CurrentDomain.GetAssemblies() |
            Where-Object { -not [string]::IsNullOrEmpty($_.Location) } |
            ForEach-Object Location | Select-Object -Unique)
    }
    Add-Type -ReferencedAssemblies $selectorAssemblies @"
using System;
using System.Drawing;
using System.Windows.Forms;
public sealed class CaptureRegionSelector : Form {
    readonly Bitmap snapshot;
    readonly Rectangle desktopBounds;
    Point start;
    Rectangle selection;
    bool dragging;
    public Rectangle SelectedRegion { get; private set; }
    public CaptureRegionSelector(Bitmap image, Rectangle screenBounds, string label) {
        snapshot = image;
        desktopBounds = screenBounds;
        Text = label;
        FormBorderStyle = FormBorderStyle.None;
        AutoScaleMode = AutoScaleMode.None;
        StartPosition = FormStartPosition.Manual;
        Bounds = screenBounds;
        TopMost = true;
        ShowInTaskbar = false;
        KeyPreview = true;
        Cursor = Cursors.Cross;
        DoubleBuffered = true;
    }
    protected override void OnPaint(PaintEventArgs e) {
        base.OnPaint(e);
        e.Graphics.DrawImageUnscaled(snapshot, 0, 0);
        using (var shade = new SolidBrush(Color.FromArgb(100, Color.Black)))
            e.Graphics.FillRectangle(shade, ClientRectangle);
        if (selection.Width > 0 && selection.Height > 0) {
            e.Graphics.DrawImage(snapshot, selection, selection, GraphicsUnit.Pixel);
            using (var pen = new Pen(Color.Yellow, 3))
                e.Graphics.DrawRectangle(pen, selection);
        }
        using (var background = new SolidBrush(Color.FromArgb(210, Color.Black)))
            e.Graphics.FillRectangle(background, 12, 12, 490, 38);
        using (var font = new Font("Segoe UI", 12, FontStyle.Bold))
        using (var brush = new SolidBrush(Color.White))
            e.Graphics.DrawString("Select " + Text + " - drag mouse; Esc cancels", font, brush, 20, 19);
    }
    protected override void OnMouseDown(MouseEventArgs e) {
        base.OnMouseDown(e);
        if (e.Button != MouseButtons.Left) return;
        start = e.Location;
        selection = Rectangle.Empty;
        dragging = true;
        Capture = true;
    }
    protected override void OnMouseMove(MouseEventArgs e) {
        base.OnMouseMove(e);
        if (!dragging) return;
        int x = Math.Max(0, Math.Min(ClientSize.Width, e.X));
        int y = Math.Max(0, Math.Min(ClientSize.Height, e.Y));
        selection = Rectangle.FromLTRB(Math.Min(start.X, x), Math.Min(start.Y, y),
            Math.Max(start.X, x), Math.Max(start.Y, y));
        Invalidate();
    }
    protected override void OnMouseUp(MouseEventArgs e) {
        base.OnMouseUp(e);
        if (!dragging || e.Button != MouseButtons.Left) return;
        OnMouseMove(e);
        dragging = false;
        Capture = false;
        if (selection.Width == 0 || selection.Height == 0) return;
        SelectedRegion = new Rectangle(desktopBounds.X + selection.X, desktopBounds.Y + selection.Y,
            selection.Width, selection.Height);
        DialogResult = DialogResult.OK;
        Close();
    }
    protected override void OnKeyDown(KeyEventArgs e) {
        if (e.KeyCode == Keys.Escape) { DialogResult = DialogResult.Cancel; Close(); }
        base.OnKeyDown(e);
    }
    protected override void OnShown(EventArgs e) {
        base.OnShown(e);
        Activate();
        Focus();
    }
}
public static class CaptureFrameDiff {
    public static double Ratio(Bitmap before, Bitmap after, Rectangle area) {
        if (before.Width != after.Width || before.Height != after.Height) return 1;
        int changed = 0, checkedPixels = 0;
        // Sample the editor and filmstrip, not the browser tab strip.
        int top = area.Top + Math.Min(100, area.Height / 8);
        for (int y = top; y < area.Bottom; y += 4)
            for (int x = area.Left; x < area.Right; x += 4) {
                Color a = before.GetPixel(x, y), b = after.GetPixel(x, y);
                if (Math.Abs(a.R - b.R) + Math.Abs(a.G - b.G) + Math.Abs(a.B - b.B) > 90)
                    changed++;
                checkedPixels++;
            }
        return checkedPixels == 0 ? 0 : (double)changed / checkedPixels;
    }
}
"@
}

# Load SendKey type only once
if (-not ("SendKey" -as [type])) {
    Add-Type @"
using System;
using System.Runtime.InteropServices;

public class SendKey {
    [DllImport("user32.dll")]
    public static extern void keybd_event(byte bVk, byte bScan, int dwFlags, int dwExtraInfo);

    public const int KEYEVENTF_KEYDOWN = 0;
    public const int KEYEVENTF_KEYUP = 2;
    public const int KEYEVENTF_EXTENDEDKEY = 1;
}
"@
}

if (-not ("SlidesWindow" -as [type])) {
    Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class SlidesWindow {
    [StructLayout(LayoutKind.Sequential)]
    public struct Rect { public int Left, Top, Right, Bottom; }
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr handle);
    [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr handle);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr handle);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr handle, out Rect rect);
    [DllImport("dwmapi.dll")] public static extern int DwmGetWindowAttribute(IntPtr handle, int attribute, out Rect rect, int size);
    [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr handle, int command);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr handle, StringBuilder text, int size);
    [DllImport("user32.dll")] static extern uint GetDpiForWindow(IntPtr handle);
    public static string Title(IntPtr handle) {
        var text = new StringBuilder(1024);
        GetWindowText(handle, text, text.Capacity);
        return text.ToString();
    }
    public static int Dpi(IntPtr handle) {
        try { return (int)GetDpiForWindow(handle); }
        catch (EntryPointNotFoundException) { return 0; }
    }
}
"@
}

# ----- Source and parameters -----
$outputDir = "$PSScriptRoot\captures"
$configPath = Join-Path $PSScriptRoot "config.json"
$inputPdf = $null
$sourceMode = "capture"
$delayMs = 1200 # delay between slides (ms)

# ----- Functions -----

function Take-Screenshot([System.Drawing.Rectangle]$bounds) {
    $bmp = [System.Drawing.Bitmap]::new($bounds.Width, $bounds.Height)
    $graphics = [System.Drawing.Graphics]::FromImage($bmp)
    try {
        $graphics.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
        return $bmp
    } catch {
        $bmp.Dispose()
        throw
    } finally {
        $graphics.Dispose()
    }
}

function Capture-Screen {
    return Take-Screenshot ([System.Windows.Forms.Screen]::PrimaryScreen.Bounds)
}

function Select-ScreenRegion([string]$label, [System.Drawing.Rectangle]$bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds) {
    $snapshot = Take-Screenshot $bounds
    $selector = $null
    try {
        $selector = [CaptureRegionSelector]::new($snapshot, $bounds, $label)
        if ($selector.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
            throw "Selection cancelled for $label."
        }
        return $selector.SelectedRegion
    } finally {
        if ($null -ne $selector) { $selector.Dispose() }
        $snapshot.Dispose()
    }
}

function Validate-CaptureArea($region, [System.Drawing.Rectangle]$bounds) {
    return Test-CaptureRegion $region $bounds
}

function Test-CaptureRegion($region, [System.Drawing.Rectangle]$screen) {
    if ($null -eq $region) { return $false }
    foreach ($name in @('x', 'y', 'width', 'height')) {
        if ($null -eq $region.PSObject.Properties[$name] -or
            [string]$region.$name -notmatch '^-?\d+$') { return $false }
    }
    try {
        $x = [int]$region.x; $y = [int]$region.y
        $width = [int]$region.width; $height = [int]$region.height
        return $width -gt 0 -and $height -gt 0 -and
            $x -ge $screen.X -and $y -ge $screen.Y -and
            ([long]$x + $width) -le $screen.Right -and
            ([long]$y + $height) -le $screen.Bottom
    } catch { return $false }
}

function Crop-Image([System.Drawing.Bitmap]$image, [System.Drawing.Rectangle]$region, [System.Drawing.Rectangle]$screen) {
    if (-not (Test-CaptureRegion $region $screen)) { throw "The selected region is outside the primary screen." }
    $local = [System.Drawing.Rectangle]::new(($region.X - $screen.X), ($region.Y - $screen.Y), $region.Width, $region.Height)
    return $image.Clone($local, $image.PixelFormat)
}

function Merge-SlideAndNotes([System.Drawing.Bitmap]$slide, [System.Drawing.Bitmap]$notes) {
    # Keep the slide at its original pixel size. Downscale wide notes only.
    $padding = [Math]::Max(16, [int][Math]::Round($slide.Width * 0.015))
    $gap = [Math]::Max(24, [int][Math]::Round($slide.Height * 0.025))
    $notesWidth = [Math]::Min($notes.Width, $slide.Width)
    $notesHeight = [Math]::Max(1, [int][Math]::Round($notes.Height * $notesWidth / $notes.Width))
    $canvas = [System.Drawing.Bitmap]::new(($slide.Width + 2 * $padding), ($slide.Height + $notesHeight + $gap + 2 * $padding))
    $drawing = [System.Drawing.Graphics]::FromImage($canvas)
    try {
        $drawing.Clear([System.Drawing.Color]::White)
        $drawing.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
        $drawing.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $drawing.DrawImageUnscaled($slide, $padding, $padding)
        $notesX = [int][Math]::Floor(($canvas.Width - $notesWidth) / 2)
        $notesRect = [System.Drawing.Rectangle]::new($notesX, ($slide.Height + $padding + $gap), $notesWidth, $notesHeight)
        $drawing.DrawImage($notes, $notesRect)
        return $canvas
    } catch {
        $canvas.Dispose()
        throw
    } finally {
        $drawing.Dispose()
    }
}

function Get-CaptureDpi {
    $graphics = [System.Drawing.Graphics]::FromHwnd([IntPtr]::Zero)
    try { return [int][Math]::Round($graphics.DpiX) }
    finally { $graphics.Dispose() }
}

function Load-CaptureConfig([string]$path, [System.Drawing.Rectangle]$screen) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
    try {
        $config = Get-Content -LiteralPath $path -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        if ($config.version -ne 1 -or $null -eq $config.screen -or
            $config.screen.x -ne $screen.X -or $config.screen.y -ne $screen.Y -or
            $config.screen.width -ne $screen.Width -or $config.screen.height -ne $screen.Height -or
            $config.screen.dpi -ne (Get-CaptureDpi) -or
            -not (Test-CaptureRegion $config.slide $screen) -or
            -not (Test-CaptureRegion $config.notes $screen)) {
            throw "Saved regions do not match this screen or DPI."
        }
        return $config
    } catch {
        Write-Host "Capture config is invalid ($($_.Exception.Message)). Select both regions again." -ForegroundColor Yellow
        return $null
    }
}

function Save-CaptureConfig([string]$path, [System.Drawing.Rectangle]$screen, [System.Drawing.Rectangle]$slide, [System.Drawing.Rectangle]$notes) {
    if (-not (Test-CaptureRegion $slide $screen) -or -not (Test-CaptureRegion $notes $screen)) {
        throw "Cannot save regions outside the primary screen."
    }
    $config = @{
        version = 1
        screen = @{ x = $screen.X; y = $screen.Y; width = $screen.Width; height = $screen.Height; dpi = (Get-CaptureDpi) }
        slide = @{ x = $slide.X; y = $slide.Y; width = $slide.Width; height = $slide.Height }
        notes = @{ x = $notes.X; y = $notes.Y; width = $notes.Width; height = $notes.Height }
    }
    if (Test-Path -LiteralPath $path -PathType Leaf) {
        try {
            $old = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json -ErrorAction Stop
            if ($null -ne $old.browser) { $config.browser = $old.browser }
        } catch { } # A damaged config is replaced after the user selects fresh regions.
    }
    $config | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $path -Encoding UTF8
}

function Test-SameRectangle($saved, [System.Drawing.Rectangle]$current) {
    if (-not (Validate-CaptureArea $saved $current)) { return $false }
    return $saved.x -eq $current.X -and $saved.y -eq $current.Y -and
        $saved.width -eq $current.Width -and $saved.height -eq $current.Height
}

function Load-BrowserCaptureConfig([string]$path, [System.Drawing.Rectangle]$desktop,
                                   [System.Drawing.Rectangle]$window, [int]$dpi, [string]$mode) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
    try {
        $config = Get-Content -LiteralPath $path -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        $browser = $config.browser
        if ($null -eq $browser) { return $null }
        if ($browser.mode -ne $mode) { return $null }
        if ($config.version -ne 1 -or $browser.dpi -ne $dpi -or
            -not (Test-SameRectangle $browser.desktop $desktop) -or
            -not (Test-SameRectangle $browser.window $window)) {
            throw 'Window position, size, display, DPI, or capture mode changed.'
        }
        if ($mode -eq 'slide-notes' -and
            (-not (Validate-CaptureArea $browser.slide $window) -or
             -not (Validate-CaptureArea $browser.notes $window))) {
            throw 'Saved slide or notes region is outside the browser window.'
        }
        if ($mode -eq 'single-region' -and -not (Validate-CaptureArea $browser.area $window)) {
            throw 'Saved capture region is outside the browser window.'
        }
        return $browser
    } catch {
        Write-Host "Browser capture config is invalid ($($_.Exception.Message)). Configure it again." -ForegroundColor Yellow
        return $null
    }
}

function Load-SavedRegionsForBrowser([string]$path, [System.Drawing.Rectangle]$window, [int]$dpi) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
    try {
        $config = Get-Content -LiteralPath $path -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        if ($config.version -ne 1 -or $config.screen.dpi -ne $dpi -or
            -not (Validate-CaptureArea $config.slide $window) -or
            -not (Validate-CaptureArea $config.notes $window)) { return $null }
        return @{ slide = $config.slide; notes = $config.notes }
    } catch { return $null }
}

function Save-BrowserCaptureConfig([string]$path, [System.Drawing.Rectangle]$desktop,
                                   [System.Drawing.Rectangle]$window, [int]$dpi, [string]$mode,
                                   [System.Drawing.Rectangle]$slide, [System.Drawing.Rectangle]$notes,
                                   [System.Drawing.Rectangle]$area = [System.Drawing.Rectangle]::Empty) {
    if (-not (Validate-CaptureArea $window $desktop)) { throw 'Browser window is outside the virtual desktop.' }
    if ($mode -eq 'slide-notes' -and
        (-not (Validate-CaptureArea $slide $window) -or -not (Validate-CaptureArea $notes $window))) {
        throw 'Slide or notes region is outside the browser window.'
    }
    if ($mode -eq 'single-region' -and -not (Validate-CaptureArea $area $window)) {
        throw 'Capture region is outside the browser window.'
    }
    $config = @{ version = 1 }
    if (Test-Path -LiteralPath $path -PathType Leaf) {
        try {
            $old = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json -ErrorAction Stop
            if ($old.version -eq 1) {
                foreach ($field in @('screen', 'slide', 'notes')) {
                    if ($null -ne $old.PSObject.Properties[$field]) { $config[$field] = $old.$field }
                }
            }
        } catch { }
    }
    $browser = @{
        mode = $mode; dpi = $dpi
        desktop = @{ x = $desktop.X; y = $desktop.Y; width = $desktop.Width; height = $desktop.Height }
        window = @{ x = $window.X; y = $window.Y; width = $window.Width; height = $window.Height }
    }
    if ($mode -eq 'slide-notes') {
        $browser.slide = @{ x = $slide.X; y = $slide.Y; width = $slide.Width; height = $slide.Height }
        $browser.notes = @{ x = $notes.X; y = $notes.Y; width = $notes.Width; height = $notes.Height }
    } elseif ($mode -eq 'single-region') {
        $browser.area = @{ x = $area.X; y = $area.Y; width = $area.Width; height = $area.Height }
    }
    $config.browser = $browser
    $config | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $path -Encoding UTF8
}

function Get-WindowRectangle([IntPtr]$handle) {
    $native = [SlidesWindow+Rect]::new()
    try {
        if ([SlidesWindow]::DwmGetWindowAttribute($handle, 9, [ref]$native, 16) -eq 0) {
            return [System.Drawing.Rectangle]::FromLTRB($native.Left, $native.Top, $native.Right, $native.Bottom)
        }
    } catch [System.DllNotFoundException], [System.EntryPointNotFoundException] { }
    if (-not [SlidesWindow]::GetWindowRect($handle, [ref]$native)) {
        throw 'Cannot read the browser window bounds.'
    }
    return [System.Drawing.Rectangle]::FromLTRB($native.Left, $native.Top, $native.Right, $native.Bottom)
}

function Activate-GoogleSlidesWindow([IntPtr]$handle) {
    if (-not [SlidesWindow]::IsWindow($handle)) { throw 'Google Slides browser window has closed.' }
    if ([SlidesWindow]::Title($handle) -notmatch '(?i)Google Slides|Google Trang tr[iì]nh b[aà]y') {
        throw 'The selected browser tab is no longer Google Slides.'
    }
    if ([SlidesWindow]::IsIconic($handle)) { [SlidesWindow]::ShowWindowAsync($handle, 9) | Out-Null }
    for ($attempt = 0; $attempt -lt 3; $attempt++) {
        if ([SlidesWindow]::GetForegroundWindow() -eq $handle) { return }
        [SlidesWindow]::SetForegroundWindow($handle) | Out-Null
        Start-Sleep -Milliseconds 250
    }
    if ([SlidesWindow]::GetForegroundWindow() -ne $handle) {
        throw 'Could not focus Google Slides. Activate its browser window and retry.'
    }
}

function Send-KeyChord([byte[]]$keys) {
    foreach ($key in $keys) {
        $flags = if ($key -in @(0x21, 0x22, 0x23, 0x24, 0x26, 0x28)) { [SendKey]::KEYEVENTF_EXTENDEDKEY } else { 0 }
        [SendKey]::keybd_event($key, 0, $flags, 0)
    }
    Start-Sleep -Milliseconds 60
    for ($i = $keys.Length - 1; $i -ge 0; $i--) {
        $key = $keys[$i]
        $flags = [SendKey]::KEYEVENTF_KEYUP
        if ($key -in @(0x21, 0x22, 0x23, 0x24, 0x26, 0x28)) { $flags = $flags -bor [SendKey]::KEYEVENTF_EXTENDEDKEY }
        [SendKey]::keybd_event($key, 0, $flags, 0)
    }
}

function Focus-GoogleSlidesFilmstrip([IntPtr]$handle) {
    Activate-GoogleSlidesWindow $handle
    # Google Slides editor: Ctrl+Alt+Shift+F focuses the thumbnail filmstrip.
    Send-KeyChord ([byte[]]@(0x11, 0x12, 0x10, 0x46))
    Start-Sleep -Milliseconds 200
}

function Go-ToFirstSlide([IntPtr]$handle, [int]$delay) {
    Focus-GoogleSlidesFilmstrip $handle
    Send-KeyChord ([byte[]]@(0x24)) # Home in the filmstrip
    Start-Sleep -Milliseconds $delay
}

function Test-SlideFrameChanged([System.Drawing.Bitmap]$before, [System.Drawing.Bitmap]$after,
                                [System.Drawing.Rectangle]$window, [System.Drawing.Rectangle]$desktop) {
    $local = [System.Drawing.Rectangle]::new(($window.X - $desktop.X), ($window.Y - $desktop.Y), $window.Width, $window.Height)
    $filmstripWidth = [Math]::Min(320, [int][Math]::Floor($window.Width / 4))
    $filmstrip = [System.Drawing.Rectangle]::new($local.X, $local.Y, $filmstripWidth, $local.Height)
    return [CaptureFrameDiff]::Ratio($before, $after, $local) -ge 0.0002 -and
        [CaptureFrameDiff]::Ratio($before, $after, $filmstrip) -ge 0.0002
}

function Go-ToNextSlide([IntPtr]$handle, [System.Drawing.Bitmap]$before,
                        [System.Drawing.Rectangle]$desktop, [System.Drawing.Rectangle]$window, [int]$delay) {
    for ($attempt = 1; $attempt -le 2; $attempt++) {
        Focus-GoogleSlidesFilmstrip $handle
        Send-KeyChord ([byte[]]@(0x22)) # Page Down selects the next filmstrip slide.
        Start-Sleep -Milliseconds $delay
        if ((Get-WindowRectangle $handle) -ne $window) { throw 'Browser window moved or resized during capture.' }
        $after = Take-Screenshot $desktop
        if (Test-SlideFrameChanged $before $after $window $desktop) { return $after }
        $after.Dispose()
        # Give a slow Slides page extra time before sending another key.
        Start-Sleep -Milliseconds 700
        $after = Take-Screenshot $desktop
        if (Test-SlideFrameChanged $before $after $window $desktop) { return $after }
        $after.Dispose()
        Write-Host "Slide did not visibly change; navigation retry $attempt/2." -ForegroundColor Yellow
    }
    throw 'Slide did not advance. Stopping to avoid duplicate or misordered captures.'
}

function Capture-CurrentSlideWithNotes([System.Drawing.Bitmap]$frame,
                                       [System.Drawing.Rectangle]$desktop, [System.Drawing.Rectangle]$window,
                                       [string]$mode, [System.Drawing.Rectangle]$slide,
                                       [System.Drawing.Rectangle]$notes,
                                       [System.Drawing.Rectangle]$area = [System.Drawing.Rectangle]::Empty) {
    if ($mode -eq 'full-window') { return Crop-Image $frame $window $desktop }
    if ($mode -eq 'single-region') { return Crop-Image $frame $area $desktop }
    $slideImage = $null; $notesImage = $null
    try {
        $slideImage = Crop-Image $frame $slide $desktop
        $notesImage = Crop-Image $frame $notes $desktop
        return Merge-SlideAndNotes $slideImage $notesImage
    } finally {
        if ($null -ne $slideImage) { $slideImage.Dispose() }
        if ($null -ne $notesImage) { $notesImage.Dispose() }
    }
}

function Invoke-BrowserCapture([string]$capturesDir, [string]$settingsPath, [int]$navigationDelay) {
    $pagesText = Read-Host 'How many slides do you want to capture?'
    $pages = 0
    if (-not [int]::TryParse($pagesText, [ref]$pages) -or $pages -lt 1) {
        throw 'Enter a positive whole number of slides.'
    }
    Write-Host 'Capture area: 1) Full browser window  2) Select ONE area (slide + notes)  3) Select Slide and Notes separately' -ForegroundColor Cyan
    $areaAnswer = Read-Host 'Area [1/2/3]'
    $mode = switch ($areaAnswer) {
        '1' { 'full-window' }
        '2' { 'single-region' }
        '3' { 'slide-notes' }
        default { throw 'Choose 1, 2 or 3.' }
    }
    $firstAnswer = Read-Host 'Start from the first slide? [Y/n]'
    $fromFirst = $firstAnswer -notin @('n', 'no')
    $delayAnswer = Read-Host "Wait after each slide (ms, Enter for $navigationDelay)"
    if (-not [string]::IsNullOrWhiteSpace($delayAnswer)) {
        $selectedDelay = 0
        if (-not [int]::TryParse($delayAnswer, [ref]$selectedDelay) -or $selectedDelay -lt 300 -or $selectedDelay -gt 30000) {
            throw 'Delay must be between 300 and 30000 milliseconds.'
        }
        $navigationDelay = $selectedDelay
    }

    Write-Host 'Open Google Slides in browser edit/view mode with the notes panel visible.' -ForegroundColor Cyan
    Write-Host 'Click the Google Slides browser window during this countdown:' -ForegroundColor Cyan
    for ($seconds = 5; $seconds -ge 1; $seconds--) {
        Write-Host "  $seconds..."
        Start-Sleep -Seconds 1
    }
    $handle = [SlidesWindow]::GetForegroundWindow()
    $title = [SlidesWindow]::Title($handle)
    if ($title -notmatch '(?i)Google Slides|Google Trang tr[iì]nh b[aà]y') {
        throw "The foreground window is not Google Slides: '$title'. Focus its browser tab and run again."
    }
    $desktop = [System.Windows.Forms.SystemInformation]::VirtualScreen
    $window = Get-WindowRectangle $handle
    if (-not (Validate-CaptureArea $window $desktop)) {
        throw 'Place the entire Google Slides browser window within the visible desktop, then retry.'
    }
    $dpi = [SlidesWindow]::Dpi($handle)
    if ($dpi -le 0) { $dpi = Get-CaptureDpi }
    Write-Host "Selected window: $title | DPI: $dpi" -ForegroundColor Green

    $saved = Load-BrowserCaptureConfig $settingsPath $desktop $window $dpi $mode
    $reuse = $false
    if ($null -ne $saved) {
        if ($mode -eq 'single-region') {
            $reuseAnswer = Read-Host 'Reuse saved single area? [y/N]'
            $reuse = $reuseAnswer -in @('y', 'yes')
        } else {
            $reuseAnswer = Read-Host 'Reuse saved browser capture area? [Y/n]'
            $reuse = $reuseAnswer -notin @('n', 'no')
        }
    }
    $slide = [System.Drawing.Rectangle]::Empty
    $notes = [System.Drawing.Rectangle]::Empty
    $area = [System.Drawing.Rectangle]::Empty
    if ($mode -eq 'single-region') {
        if ($reuse) {
            $area = [System.Drawing.Rectangle]::new($saved.area.x, $saved.area.y, $saved.area.width, $saved.area.height)
        } else {
            Write-Host 'Drag ONE rectangle around both the main slide and the visible notes below it.' -ForegroundColor Cyan
            for ($attempt = 1; $attempt -le 3; $attempt++) {
                Activate-GoogleSlidesWindow $handle
                Start-Sleep -Milliseconds 250
                $area = Select-ScreenRegion 'Slide + visible notes' $desktop
                if (Validate-CaptureArea $area $window) { break }
                Write-Host "Selected $area is outside browser window $window. Try again." -ForegroundColor Yellow
            }
            if (-not (Validate-CaptureArea $area $window)) {
                throw 'Could not select one area inside the browser window.'
            }
        }
        Write-Host "Using one capture area: $area" -ForegroundColor Green
    }
    if ($mode -eq 'slide-notes') {
        $regions = if ($reuse) { $saved } else { $null }
        if ($null -eq $regions) {
            $olderRegions = Load-SavedRegionsForBrowser $settingsPath $window $dpi
            if ($null -ne $olderRegions) {
                $olderAnswer = Read-Host 'Reuse existing Slide/Notes coordinates from config.json? [Y/n]'
                if ($olderAnswer -notin @('n', 'no')) { $regions = $olderRegions }
            }
        }
        if ($null -ne $regions) {
            $slide = [System.Drawing.Rectangle]::new($regions.slide.x, $regions.slide.y, $regions.slide.width, $regions.slide.height)
            $notes = [System.Drawing.Rectangle]::new($regions.notes.x, $regions.notes.y, $regions.notes.width, $regions.notes.height)
            Write-Host "Using Slide $slide and Notes $notes" -ForegroundColor Green
        } else {
            foreach ($label in @('Slide', 'Visible notes')) {
                $selected = [System.Drawing.Rectangle]::Empty
                for ($attempt = 1; $attempt -le 3; $attempt++) {
                    Activate-GoogleSlidesWindow $handle
                    Start-Sleep -Milliseconds 250
                    Write-Host "Drag to select $label inside the browser window (attempt $attempt/3). Press Esc to cancel." -ForegroundColor Cyan
                    $selected = Select-ScreenRegion $label $desktop
                    if (Validate-CaptureArea $selected $window) { break }
                    Write-Host "Selected $selected is outside browser window $window." -ForegroundColor Yellow
                }
                if (-not (Validate-CaptureArea $selected $window)) {
                    throw "Could not select $label inside the browser window. Move or maximize the browser and retry."
                }
                if ($label -eq 'Slide') { $slide = $selected } else { $notes = $selected }
            }
        }
    }
    if (-not $reuse) {
        Save-BrowserCaptureConfig $settingsPath $desktop $window $dpi $mode $slide $notes $area
        Write-Host "Browser capture area saved to: $settingsPath" -ForegroundColor Green
    }

    if ($fromFirst) { Go-ToFirstSlide $handle $navigationDelay }
    else {
        Focus-GoogleSlidesFilmstrip $handle
        Start-Sleep -Milliseconds $navigationDelay
    }
    if ((Get-WindowRectangle $handle) -ne $window) {
        throw 'Browser window moved or resized after selecting the capture area.'
    }
    New-Item -ItemType Directory -Path $capturesDir -Force | Out-Null
    Get-ChildItem -LiteralPath $capturesDir -File |
        Where-Object { $_.Name -match '^(?i)Page_\d+.*\.(png|jpe?g|bmp|webp)$' } | Remove-Item -Force

    $frame = Take-Screenshot $desktop
    try {
        for ($page = 1; $page -le $pages; $page++) {
            Activate-GoogleSlidesWindow $handle
            $image = Capture-CurrentSlideWithNotes $frame $desktop $window $mode $slide $notes $area
            try {
                $filename = Join-Path $capturesDir "Page_$page.png"
                $image.Save($filename, [System.Drawing.Imaging.ImageFormat]::Png)
                Write-Host "Captured slide $page/$pages -> $filename"
            } finally { $image.Dispose() }
            if ($page -lt $pages) {
                $next = Go-ToNextSlide $handle $frame $desktop $window $navigationDelay
                $frame.Dispose()
                $frame = $next
            }
        }
    } finally { if ($null -ne $frame) { $frame.Dispose() } }
    Write-Host "Sequential capture complete: $pages slides in $capturesDir" -ForegroundColor Green
}

function Press-RightArrow {
    $VK_RIGHT = 0x27
    [SendKey]::keybd_event($VK_RIGHT, 0, [SendKey]::KEYEVENTF_KEYDOWN, 0)
    Start-Sleep -Milliseconds 10
    [SendKey]::keybd_event($VK_RIGHT, 0, [SendKey]::KEYEVENTF_KEYUP, 0)
}

Write-Host ""
Write-Host "Choose the source:" -ForegroundColor Cyan
Write-Host "  1) Capture slide only"
Write-Host "  2) Capture slide + speaker notes"
Write-Host "  3) Reuse captures"
Write-Host "  4) Process existing PDF"
Write-Host "  5) Capture sequentially in Google Slides view/edit mode (slide + notes)"
$sourceChoice = Read-Host "Source [1/2/3/4/5]"

switch ($sourceChoice) {
    "2" { $sourceMode = "capture-notes" }
    "3" { $sourceMode = "captures" }
    "4" { $sourceMode = "pdf" }
    "5" { $sourceMode = "browser" }
    default { $sourceMode = "capture" }
}

if ($sourceMode -eq "browser") {
    Invoke-BrowserCapture $outputDir $configPath $delayMs
} elseif ($sourceMode -eq "capture" -or $sourceMode -eq "capture-notes") {
    $pages = Read-Host "How many pages do you want to capture?"
    if (-not ($pages -as [int]) -or $pages -lt 1) {
        Write-Host "Invalid number. Aborting."
        exit
    }
    $screen = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
    if ($sourceMode -eq "capture-notes") {
        $config = Load-CaptureConfig $configPath $screen
        $reuse = $false
        if ($null -ne $config) {
            $reuseAnswer = Read-Host "Reuse saved slide and notes regions? [Y/n]"
            $reuse = $reuseAnswer -notin @('n', 'N', 'no', 'NO')
        }
        if ($reuse) {
            $slideRegion = [System.Drawing.Rectangle]::new($config.slide.x, $config.slide.y, $config.slide.width, $config.slide.height)
            $notesRegion = [System.Drawing.Rectangle]::new($config.notes.x, $config.notes.y, $config.notes.width, $config.notes.height)
        } else {
            Write-Host "Show Presenter View on the primary screen, then select Slide and Speaker Notes." -ForegroundColor Cyan
            $slideRegion = Select-ScreenRegion "Slide"
            $notesRegion = Select-ScreenRegion "Speaker Notes"
            Save-CaptureConfig $configPath $screen $slideRegion $notesRegion
            Write-Host "Regions saved to: $configPath" -ForegroundColor Green
        }
    }
    New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
    Get-ChildItem -LiteralPath $outputDir -File | Where-Object { $_.Name -match '^(?i)Page_\d+.*\.(png|jpe?g|bmp|webp)$' } | Remove-Item -Force

# ----- Countdown: 5 to 1 -----
Write-Host ""
Write-Host "Recording is about to start !" -ForegroundColor Cyan
Write-Host "Make sure the Google Slides presentation window is active (Present mode)." -ForegroundColor Cyan
Write-Host ""
for ($i = 5; $i -ge 1; $i--) {
    Write-Host -NoNewline "`rRecording starts in $i s..." -ForegroundColor Red
    Start-Sleep -Seconds 1
}
# Clear countdown line
Write-Host -NoNewline ("`r" + (" " * 40) + "`r")
# Final message
[console]::beep(1000, 500)  
Write-Host "Recording live !" -ForegroundColor Red

# ----- Capture loop -----
for ($i = 1; $i -le $pages; $i++) {

    $filename = Join-Path $outputDir ("Page_$i.png")
    Write-Host "Capture $i / $pages -> $filename"

    $screenshot = Capture-Screen
    try {
        if ($sourceMode -eq "capture-notes") {
            $slideImage = $null
            $notesImage = $null
            try {
                $slideImage = Crop-Image $screenshot $slideRegion $screen
                $notesImage = Crop-Image $screenshot $notesRegion $screen
                $merged = Merge-SlideAndNotes $slideImage $notesImage
                try { $merged.Save($filename, [System.Drawing.Imaging.ImageFormat]::Png) }
                finally { $merged.Dispose() }
            } finally {
                if ($null -ne $slideImage) { $slideImage.Dispose() }
                if ($null -ne $notesImage) { $notesImage.Dispose() }
            }
        } else {
            $screenshot.Save($filename, [System.Drawing.Imaging.ImageFormat]::Png)
        }
    } finally { $screenshot.Dispose() }
    Press-RightArrow

    Start-Sleep -Milliseconds $delayMs
}

Write-Host "Recording done ! $pages capture(s) saved in: $outputDir" -ForegroundColor Red
} elseif ($sourceMode -eq "captures") {
    if (-not (Test-Path -LiteralPath $outputDir -PathType Container)) {
        Write-Host "Captures folder not found: $outputDir" -ForegroundColor Red
        exit
    }
    $existingCaptures = @(
        Get-ChildItem -LiteralPath $outputDir -File |
            Where-Object { $_.Name -match '^(?i)Page_\d+.*\.(png|jpe?g|bmp|webp)$' }
    )
    if ($existingCaptures.Count -eq 0) {
        Write-Host "No Page_N images found in: $outputDir" -ForegroundColor Red
        exit
    }
    Write-Host "Reusing $($existingCaptures.Count) existing capture(s) from: $outputDir" -ForegroundColor Green
} else {
    $rawPdfPath = Read-Host "Path to the existing PDF"
    $rawPdfPath = $rawPdfPath.Trim().Trim('"')
    if ([string]::IsNullOrWhiteSpace($rawPdfPath) -or -not (Test-Path -LiteralPath $rawPdfPath -PathType Leaf)) {
        Write-Host "PDF not found: $rawPdfPath" -ForegroundColor Red
        exit
    }
    $inputPdf = (Resolve-Path -LiteralPath $rawPdfPath).Path
    if ([System.IO.Path]::GetExtension($inputPdf).ToLowerInvariant() -ne ".pdf") {
        Write-Host "The selected file is not a PDF: $inputPdf" -ForegroundColor Red
        exit
    }
    Write-Host "Existing PDF selected: $inputPdf" -ForegroundColor Green
}

# ===============================
#   CHOOSE OUTPUT FORMAT(S)
# ===============================

if ($sourceMode -eq "pdf") {
    Write-Host "Existing searchable text is reused; Tesseract runs only where needed." -ForegroundColor Cyan
    $choice = "4"
} else {
    Write-Host ""
    Write-Host "Select output format:" -ForegroundColor Cyan
    Write-Host "  1) PDF only   (img-2-pdf.py)"
    Write-Host "  2) DOCX only  (img-2-docx.py)"
    Write-Host "  3) Both PDF and DOCX (both .py script)"
    Write-Host "  4) Searchable PDF + auto table of contents (OCR)"
    $choice = Read-Host "Your choice [1/2/3/4]"
}

$doPdf  = $false
$doDocx = $false
$doOcr  = $false
$darkMode = $false

switch ($choice) {
    "1" { $doPdf  = $true }
    "2" { $doDocx = $true }
    "3" { $doPdf  = $true; $doDocx = $true }
    "4" { $doOcr  = $true }
    default {
        Write-Host "Invalid choice, defaulting to PDF only."
        $doPdf = $true
    }
}

if ($doOcr) {
    Write-Host ""
    Write-Host "Optional display mode" -ForegroundColor Cyan
    Write-Host "  1) Keep original slide colors (default)"
    Write-Host "  2) Smart dark mode for black-on-white slides"
    Write-Host "Dark mode preserves colored content and skips slides that are already dark or photographic."
    $displayChoice = Read-Host "Display [1/2]"
    if ($displayChoice -eq "2") {
        $darkMode = $true
    }
}

# Default file names produced by Python scripts
$defaultPdf  = Join-Path $PSScriptRoot "result.pdf"
$defaultDocx = Join-Path $PSScriptRoot "result.docx"
$defaultOcr  = Join-Path $PSScriptRoot "result-searchable.pdf"
if ($inputPdf -and [System.IO.Path]::GetFullPath($inputPdf) -eq [System.IO.Path]::GetFullPath($defaultOcr)) {
    $defaultOcr = Join-Path $PSScriptRoot "result-searchable-new.pdf"
    Write-Host "The source PDF is protected; output will use: $defaultOcr" -ForegroundColor Yellow
}

$generatedPdf  = $null
$generatedDocx = $null
$generatedOcr  = $null

# ----- Run img-2-pdf.py if requested -----
if ($doPdf) {
    Write-Host "`nRunning img-2-pdf.py..."
    python "$PSScriptRoot\img-2-pdf.py"

    if (Test-Path $defaultPdf) {
        $generatedPdf = $defaultPdf
        Write-Host "PDF created: $defaultPdf"
    } else {
        Write-Host "WARNING: result.pdf not found. Something went wrong in img-2-pdf.py."
    }
}

# ----- Run img-2-docx.py if requested -----
if ($doDocx) {
    Write-Host "`nRunning img-2-docx.py..."
    python "$PSScriptRoot\img-2-docx.py"

    if (Test-Path $defaultDocx) {
        $generatedDocx = $defaultDocx
        Write-Host "DOCX created: $defaultDocx"
    } else {
        Write-Host "WARNING: result.docx not found. Something went wrong in img-2-docx.py."
    }
}

# ----- Run img-2-searchable-pdf.py if requested -----
if ($doOcr) {
    Write-Host "`nRunning img-2-searchable-pdf.py (OCR + table of contents, this can take a while)..."
    $ocrArguments = @("$PSScriptRoot\img-2-searchable-pdf.py")
    if ($inputPdf) {
        $ocrArguments += "--input-pdf"
        $ocrArguments += $inputPdf
        $ocrArguments += "--output"
        $ocrArguments += $defaultOcr
    }
    if ($darkMode) {
        $ocrArguments += "--dark-mode"
    }

    $ocrExitCode = 1
    & python @ocrArguments
    $ocrExitCode = $LASTEXITCODE

    if ($ocrExitCode -eq 0 -and (Test-Path $defaultOcr)) {
        $generatedOcr = $defaultOcr
        Write-Host "Searchable PDF created: $defaultOcr"
    } else {
        Write-Host "WARNING: searchable PDF generation failed (exit code $ocrExitCode)."
    }
}

if (-not $generatedPdf -and -not $generatedDocx -and -not $generatedOcr) {
    Write-Host "No output file was generated. Exiting."
    exit
}

# ===============================
#   RENAME OUTPUT(S)
# ===============================
Write-Host ""
$baseName = Read-Host "Name your output file (without extension, leave empty to keep 'result')"

if (-not [string]::IsNullOrWhiteSpace($baseName)) {

    # ----- Rename PDF if it exists -----
    if ($generatedPdf) {
        $newPdfName = $baseName
        if (-not $newPdfName.ToLower().EndsWith(".pdf")) {
            $newPdfName += ".pdf"
        }
        $finalPdf = Join-Path $PSScriptRoot $newPdfName

        if (Test-Path $finalPdf) {
            Remove-Item $finalPdf -Force
        }

        Rename-Item -Path $generatedPdf -NewName $newPdfName
        $generatedPdf = $finalPdf
    }

    # ----- Rename DOCX if it exists -----
    if ($generatedDocx) {
        $newDocxName = $baseName
        if (-not $newDocxName.ToLower().EndsWith(".docx")) {
            $newDocxName += ".docx"
        }
        $finalDocx = Join-Path $PSScriptRoot $newDocxName

        if (Test-Path $finalDocx) {
            Remove-Item $finalDocx -Force
        }

        Rename-Item -Path $generatedDocx -NewName $newDocxName
        $generatedDocx = $finalDocx
    }

    # ----- Rename searchable PDF if it exists -----
    if ($generatedOcr) {
        $newOcrName = $baseName
        if (-not $newOcrName.ToLower().EndsWith(".pdf")) {
            $newOcrName += ".pdf"
        }
        $finalOcr = Join-Path $PSScriptRoot $newOcrName

        if ($inputPdf -and [System.IO.Path]::GetFullPath($finalOcr) -eq [System.IO.Path]::GetFullPath($inputPdf)) {
            $safeStem = [System.IO.Path]::GetFileNameWithoutExtension($newOcrName)
            $newOcrName = "$safeStem-searchable.pdf"
            $finalOcr = Join-Path $PSScriptRoot $newOcrName
            Write-Host "The source PDF will not be overwritten. Using: $newOcrName" -ForegroundColor Yellow
        }

        if ([System.IO.Path]::GetFullPath($generatedOcr) -ne [System.IO.Path]::GetFullPath($finalOcr)) {
            if (Test-Path $finalOcr) {
                Remove-Item $finalOcr -Force
            }
            Rename-Item -Path $generatedOcr -NewName $newOcrName
        }
        $generatedOcr = $finalOcr
    }

} else {
    # Keep default names
    if ($generatedPdf)  { $generatedPdf  = $defaultPdf }
    if ($generatedDocx) { $generatedDocx = $defaultDocx }
    if ($generatedOcr)  { $generatedOcr  = $defaultOcr }
}

# ===============================
#   OPEN OUTPUT(S)
# ===============================

if ($generatedPdf -and (Test-Path $generatedPdf)) {
    Write-Host "Opening PDF: $generatedPdf"
    Start-Process $generatedPdf
}

if ($generatedDocx -and (Test-Path $generatedDocx)) {
    Write-Host "Opening DOCX: $generatedDocx"
    Start-Process $generatedDocx
}

if ($generatedOcr -and (Test-Path $generatedOcr)) {
    Write-Host "Opening searchable PDF: $generatedOcr"
    Start-Process $generatedOcr
}

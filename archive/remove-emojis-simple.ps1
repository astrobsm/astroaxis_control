# Simple emoji removal script
$inputFile = "C:\Users\USER\ASTROAXIS\frontend\src\AppMain.js"
$outputFile = "C:\Users\USER\ASTROAXIS\frontend\src\AppMain_fixed.js"

# Read file content
$content = Get-Content $inputFile -Raw -Encoding UTF8

# Simple string replacements to remove specific emoji characters
$content = $content -replace "ðŸ"ˆ ", ""
$content = $content -replace "💳 ", ""
$content = $content -replace "🛒 ", ""
$content = $content -replace "📦 ", ""
$content = $content -replace "👥 ", ""
$content = $content -replace "🏭 ", ""
$content = $content -replace "👤 ", ""

# Write to output file
$content | Set-Content $outputFile -Encoding UTF8 -NoNewline

Write-Host "Emojis removed successfully!"
Write-Host "Fixed file saved as: $outputFile"
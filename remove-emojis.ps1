# Remove emojis from AppMain.js
$filePath = "C:\Users\USER\ASTROAXIS\frontend\src\AppMain.js"
$content = Get-Content $filePath -Raw -Encoding UTF8

# Remove specific emojis with their text
$content = $content -replace "ðŸ"Š Dashboard", "Dashboard"
$content = $content -replace "ðŸ•' Attendance Management", "Attendance Management"
$content = $content -replace "ðŸ"¦ Products", "Products"
$content = $content -replace "ðŸ§± Raw Materials", "Raw Materials"
$content = $content -replace "ðŸ­ Production", "Production"
$content = $content -replace "ðŸ'° Sales Orders", "Sales Orders"
$content = $content -replace "ðŸ"¦ Stock Management", "Stock Management"
$content = $content -replace "ðŸ"ˆ Reports", "Reports"
$content = $content -replace "š™ï¸ Settings", "Settings"

# Remove dashboard section emojis
$content = $content -replace "💳 Payment Status", "Payment Status"
$content = $content -replace "🛒 Sales Summary", "Sales Summary"
$content = $content -replace "👥 Staff & Attendance", "Staff & Attendance"
$content = $content -replace "🏭 Production Status", "Production Status"

# Remove any remaining emoji characters using Unicode ranges
$content = $content -replace "[\u{1F600}-\u{1F64F}]", ""  # Emoticons
$content = $content -replace "[\u{1F300}-\u{1F5FF}]", ""  # Symbols & Pictographs
$content = $content -replace "[\u{1F680}-\u{1F6FF}]", ""  # Transport & Map
$content = $content -replace "[\u{1F1E0}-\u{1F1FF}]", ""  # Flags
$content = $content -replace "[\u{2600}-\u{26FF}]", ""    # Miscellaneous Symbols
$content = $content -replace "[\u{2700}-\u{27BF}]", ""    # Dingbats

# Save the cleaned file
$content | Out-File $filePath -Encoding UTF8 -NoNewline

Write-Host "Emojis removed from AppMain.js successfully!" -ForegroundColor Green
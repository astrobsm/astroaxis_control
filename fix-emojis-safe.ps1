# Careful emoji removal
$file = "C:\Users\USER\ASTROAXIS\frontend\src\AppMain.js"

# Read the entire file as a single string
$content = [System.IO.File]::ReadAllText($file, [System.Text.Encoding]::UTF8)

# Replace corrupted emoji with clean text
$content = $content.Replace("Reports", "Reports")

# Handle other emojis
$content = $content.Replace("Payment Status", "Payment Status")
$content = $content.Replace("Sales Summary", "Sales Summary") 
$content = $content.Replace("Inventory Overview", "Inventory Overview")
$content = $content.Replace("Staff `& Attendance", "Staff `& Attendance")
$content = $content.Replace("Production Status", "Production Status")
$content = $content.Replace("Customers", "Customers")
$content = $content.Replace("Inventory Settings", "Inventory Settings")

# Write back to file
[System.IO.File]::WriteAllText($file, $content, [System.Text.Encoding]::UTF8)

Write-Host "Emoji removal completed!"
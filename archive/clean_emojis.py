#!/usr/bin/env python3
"""
COMPREHENSIVE EMOJI REMOVAL SCRIPT FOR ASTRO-ASIX ERP
This script removes all emojis from the entire application.
"""

import re
import os
from pathlib import Path

def remove_emojis_comprehensive(text):
    """Remove emojis comprehensively from text"""
    
    # Define comprehensive emoji replacement mappings
    emoji_replacements = {
        # Module headers
        'ðŸ'¥': '',  # Staff
        '⏰': '',   # Attendance  
        'ðŸ"¦': '',  # Products
        'ðŸ§±': '',  # Raw Materials
        'ðŸ­': '',   # Production
        'ðŸ›'': '',  # Sales
        'ðŸ"ˆ': '',  # Reports
        'š™ï¸': '', # Settings
        'ðŸ"Š': '',  # Dashboard
        
        # Action buttons
        'ž•': '[Add]',
        'âž•': '[Add]',
        'ðŸ"¥': '[Import]',
        'ðŸ"„': '[Export]',
        'ðŸ"‹': '[List]',
        'œï¸': '[Edit]', 
        'âœï¸': '[Edit]',
        'ðŸ—'ï¸': '[Delete]',
        'ðŸ—'': '[Delete]',
        'ðŸ"': '[View]',
        'ðŸ"': '[Search]',
        'ðŸ"„': '[Refresh]',
        'ðŸ"': '[Download]',
        'âš™ï¸': '[Settings]',
        
        # Status indicators
        'âœ…': '[OK]',
        'â—': '[ERROR]',
        'âš ï¸': '[WARNING]',
        'âš ': '[WARNING]',
        'ðŸŸ¢': '[ACTIVE]',
        'ðŸ"´': '[INACTIVE]',
        'ðŸŸ¡': '[PENDING]',
        
        # Business icons
        'ðŸ'°': '',
        'ðŸ'³': '',
        'ðŸ'¼': '',
        'ðŸ¢': '',
        'ðŸŒ': '',
        'ðŸ"±': '',
        'ðŸ'»': '',
        'ðŸ–¨ï¸': '',
        
        # UI elements
        'ðŸŽ¯': '',
        'ðŸš€': '',
        'ðŸŽ‰': '',
        'ðŸ"¥': '',
        'â­': '',
        'ðŸ†': '',
        'ðŸ'': '',
        
        # Common encoded emojis from the file
        'ð\x9f\x91¥': '',
        'ð\x9f\x93¦': '',
        'ð\x9f\xa7±': '',
        'ð\x9f\x8f­': '',
        'ð\x9f\x93ˆ': '',
        'ð\x9f\x9b'': '',
        'ð\x9f\x93Š': '',
        
        # Remove other problematic characters
        'š™': '',
        'ï¸': '',
        'ž': '',
        'ð': '',
        '🧹': '',
        '📝': '',
        '🤖': '',
        '🎯': '',
        '📊': '',
        '🔍': '',
        '📦': '',
        '💰': '',
        '⚠️': '[WARNING]',
        '✅': '[OK]',
        '❌': '[ERROR]',
        '🧱': '',
        '👥': '',
        '📈': '',
        '🛒': '',
        '🏭': '',
        '⚙️': '',
        '📱': '',
        '💳': '',
        '👤': '',
        '🏢': '',
        '🌍': '',
        '🔒': '',
        '💼': '',
        '➕': '[Add]',
        '📥': '[Import]',
        '🔄': '[Refresh]',
        '✏️': '[Edit]',
        '🗑️': '[Delete]',
    }
    
    # Apply replacements
    result = text
    for emoji, replacement in emoji_replacements.items():
        result = result.replace(emoji, replacement)
    
    # Remove any remaining Unicode emoji patterns
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"  # emoticons
        "\U0001F300-\U0001F5FF"  # symbols & pictographs
        "\U0001F680-\U0001F6FF"  # transport & map symbols
        "\U0001F1E0-\U0001F1FF"  # flags
        "\U00002500-\U00002BEF"  # chinese chars
        "\U00002702-\U000027B0"
        "\U000024C2-\U0001F251"
        "\U0001f926-\U0001f937"
        "\U00010000-\U0010ffff"
        "\u2640-\u2642"
        "\u2600-\u2B55"
        "\u200d"
        "\u23cf"
        "\u23e9"
        "\u231a"
        "\ufe0f"
        "\u3030"
        "]+",
        flags=re.UNICODE
    )
    
    result = emoji_pattern.sub('', result)
    
    # Clean up extra spaces
    result = re.sub(r'\s+', ' ', result)
    result = re.sub(r'^\s+|\s+$', '', result, flags=re.MULTILINE)
    
    return result

# Process the main AppMain.js file
print("Starting comprehensive emoji removal from AppMain.js...")

with open(r'C:\Users\USER\ASTROAXIS\frontend\src\AppMain.js', 'r', encoding='utf-8') as f:
    content = f.read()

print(f"Original file size: {len(content)} characters")

# Apply comprehensive emoji removal
content = remove_emojis_comprehensive(content)

print(f"Cleaned file size: {len(content)} characters")

# Write back the cleaned content
with open(r'C:\Users\USER\ASTROAXIS\frontend\src\AppMain.js', 'w', encoding='utf-8') as f:
    f.write(content)

print("✓ Emoji removal completed for AppMain.js")

# Also clean any markdown files
md_files = [
    'README.md',
    'COMPREHENSIVE_TESTING_REPORT.md',
    'PWA_SUCCESS_REPORT.md',
    'DEPLOYMENT_SUCCESS_REPORT.md'
]

for md_file in md_files:
    if os.path.exists(md_file):
        try:
            with open(md_file, 'r', encoding='utf-8') as f:
                md_content = f.read()
            
            cleaned_md = remove_emojis_comprehensive(md_content)
            
            with open(md_file, 'w', encoding='utf-8') as f:
                f.write(cleaned_md)
            
            print(f"✓ Cleaned emojis from {md_file}")
        except Exception as e:
            print(f"Error cleaning {md_file}: {e}")

print("\n🎉 All emojis removed from application files!")
print("The application now has a clean, professional appearance without emojis.")
    '📊': '',
    '🎂': '',
    '🎉': '',
    '💰': '',
    '🏭': '',
    '🕐': '',
    '👨‍💼': '',
    '👥': '',
    '🏢': '',
    '🌍': '',
    '🔒': '',
    '🧩': '',
    '📱': '',
    '◀': '<',
    '▶': '>',
    '↻': '',
}

for old, new in replacements.items():
    content = content.replace(old, new)

# Remove any remaining emoji characters (Unicode range)
content = re.sub(r'[\U0001F300-\U0001F9FF]', '', content)

# Write back
with open(r'C:\Users\USER\ASTROAXIS\frontend\src\AppMain.js', 'w', encoding='utf-8') as f:
    f.write(content)

print("✓ Staff table fixed and emojis removed from AppMain.js")

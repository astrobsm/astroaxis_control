#!/usr/bin/env python3
"""
Comprehensive emoji cleanup script for React ERP application
Removes all emojis from AppMain.js and applies professional interface styling
"""

import re
import os

def clean_emojis_from_content(content):
    """Remove all emojis from content using comprehensive regex patterns"""
    
    # Unicode emoji ranges
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"  # emoticons
        "\U0001F300-\U0001F5FF"  # symbols & pictographs
        "\U0001F680-\U0001F6FF"  # transport & map symbols
        "\U0001F1E0-\U0001F1FF"  # flags (iOS)
        "\U00002700-\U000027BF"  # dingbats
        "\U0001f926-\U0001f937"  # extended emoticons
        "\U00010000-\U0010ffff"  # supplementary symbols
        "\u2640-\u2642"          # gender symbols
        "\u2600-\u2B55"          # misc symbols
        "\u200d"                 # zero width joiner
        "\u23cf"                 # eject symbol
        "\u23e9"                 # fast forward
        "\u231a"                 # watch
        "\ufe0f"                 # variation selector
        "\u3030"                 # wavy dash
        "]+", flags=re.UNICODE)
    
    # Remove emojis using regex
    content = emoji_pattern.sub('', content)
    
    # Replace specific emoji text that might remain
    emoji_text_replacements = {
        '📈': '',
        '💳': '',
        '🛒': '',
        '📦': '',
        '👥': '',
        '🏭': '',
        '👤': '',
        '⏰': '',
        '🧱': '',
        '🔧': '',
        '🚦': '',
        '💰': '',
        '⚙': '',
        '📄': '',
        '📱': '',
        '🎯': '',
        '📊': '',
        '📋': '',
        '🔍': '',
        '✅': '',
        '❌': '',
        '⚠': '',
        '📝': '',
        '🗂': '',
        '📈': '',
        '💼': '',
        '📞': '',
        '📧': '',
        '🏢': '',
        '🌟': '',
        '🔥': '',
        '💡': '',
        '🎉': '',
        '🚀': '',
        '⭐': '',
        '🎊': '',
        '🎈': '',
    }
    
    for emoji, replacement in emoji_text_replacements.items():
        content = content.replace(emoji, replacement)
    
    return content

def clean_main_app():
    """Clean emojis from AppMain.js and apply professional styling"""
    
    frontend_path = os.path.join('frontend', 'src', 'AppMain.js')
    
    if not os.path.exists(frontend_path):
        print(f"❌ File not found: {frontend_path}")
        return False
    
    print(f"🧹 Cleaning emojis from {frontend_path}")
    
    # Read the file
    with open(frontend_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Clean emojis
    original_length = len(content)
    clean_content = clean_emojis_from_content(content)
    new_length = len(clean_content)
    
    # Additional professional text replacements for module headers
    professional_replacements = {
        'Reports': 'Reports',
        'Payment Status': 'Payment Status', 
        'Sales Summary': 'Sales Summary',
        'Inventory Overview': 'Inventory Overview',
        'Staff & Attendance': 'Staff & Attendance',
        'Production Status': 'Production Status',
        'Customers': 'Customers',
        'Raw Materials': 'Raw Materials',
        'Finished Goods': 'Finished Goods',
        'BOM Management': 'BOM Management',
        'Quality Control': 'Quality Control',
        'Warehouse': 'Warehouse',
        'Settings': 'Settings',
        'Dashboard': 'Dashboard',
        'Sales Orders': 'Sales Orders',
        'Purchase Orders': 'Purchase Orders',
    }
    
    for original, replacement in professional_replacements.items():
        clean_content = clean_content.replace(f"'{original}'", f"'{replacement}'")
        clean_content = clean_content.replace(f'"{original}"', f'"{replacement}"')
    
    # Write the cleaned content back
    with open(frontend_path, 'w', encoding='utf-8') as f:
        f.write(clean_content)
    
    print(f"✅ Emoji cleanup complete!")
    print(f"📊 Removed {original_length - new_length} characters")
    print(f"📁 File updated: {frontend_path}")
    
    return True

def verify_cleanup():
    """Verify that emojis have been removed"""
    
    frontend_path = os.path.join('frontend', 'src', 'AppMain.js')
    
    if not os.path.exists(frontend_path):
        print(f"❌ Cannot verify - file not found: {frontend_path}")
        return False
    
    with open(frontend_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Check for common emoji patterns
    emoji_check = re.compile(
        "["
        "\U0001F600-\U0001F64F"  # emoticons
        "\U0001F300-\U0001F5FF"  # symbols & pictographs
        "\U0001F680-\U0001F6FF"  # transport & map symbols
        "]+", flags=re.UNICODE)
    
    emoji_matches = emoji_check.findall(content)
    
    if emoji_matches:
        print(f"⚠ Found {len(emoji_matches)} remaining emojis: {emoji_matches[:5]}")
        return False
    else:
        print("✅ No emojis detected - cleanup successful!")
        return True

def main():
    """Main execution function"""
    
    print("🚀 Starting comprehensive emoji cleanup...")
    print("=" * 50)
    
    # Check if we're in the right directory
    if not os.path.exists('frontend'):
        print("❌ Error: Not in ASTROAXIS project directory")
        print("Please run this script from the project root")
        return
    
    # Clean the main app file
    success = clean_main_app()
    
    if success:
        # Verify the cleanup
        verify_cleanup()
        
        print("\n" + "=" * 50)
        print("✅ EMOJI CLEANUP COMPLETE!")
        print("🎯 Your application now has a professional interface")
        print("📋 Next steps:")
        print("   1. Build the application: npm run build")
        print("   2. Deploy to production server")
        print("   3. Verify the interface at http://209.38.226.32")
        print("=" * 50)
    else:
        print("❌ Cleanup failed - please check the file paths")

if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""
EMOJI REMOVAL SCRIPT - Simple and Clean
"""

import re

def clean_emojis_from_file(file_path):
    """Remove emojis from a single file"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Remove all non-ASCII characters except common symbols
        # Keep: letters, numbers, spaces, punctuation, currency symbols
        cleaned = ''
        for char in content:
            # Keep ASCII printable characters
            if ord(char) < 127:
                cleaned += char
            # Keep essential currency symbols
            elif char in ['₦', '€', '£', '$']:
                cleaned += char
            # Replace emojis and special characters with spaces
            else:
                cleaned += ' '
        
        # Clean up multiple spaces
        cleaned = re.sub(r'\s+', ' ', cleaned)
        cleaned = re.sub(r'^ +| +$', '', cleaned, flags=re.MULTILINE)
        
        # Specific text replacements for better readability
        replacements = {
            ' Add ': ' [Add] ',
            ' Edit ': ' [Edit] ',
            ' Delete ': ' [Delete] ',
            ' Warning ': ' [Warning] ',
            ' Success ': ' [Success] ',
            ' Error ': ' [Error] ',
            ' Info ': ' [Info] ',
        }
        
        for old, new in replacements.items():
            cleaned = cleaned.replace(old, new)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(cleaned)
        
        print(f"Cleaned: {file_path}")
        return True
        
    except Exception as e:
        print(f"Error processing {file_path}: {e}")
        return False

# Clean the main frontend file
print("Removing emojis from AppMain.js...")
success = clean_emojis_from_file(r'C:\Users\USER\ASTROAXIS\frontend\src\AppMain.js')

if success:
    print("✓ Successfully removed emojis from AppMain.js")
else:
    print("✗ Failed to clean AppMain.js")

print("\nEmoji removal completed!")

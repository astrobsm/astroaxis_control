"""Test script for PIN-based attendance system"""
import asyncio
import json
from datetime import datetime
import aiohttp
import sys

async def test_pin_attendance():
    """Test the new PIN-based attendance endpoint"""
    base_url = "http://localhost:8000"
    
    print("🧪 Testing PIN-based Attendance System")
    print("=" * 50)
    
    async with aiohttp.ClientSession() as session:
        try:
            # First, let's check if we have any staff with PINs
            print("📋 Checking staff with PINs...")
            async with session.get(f"{base_url}/api/staff/staffs/") as response:
                if response.status == 200:
                    staff_data = await response.json()
                    staff_list = staff_data.get('items', [])
                    
                    if not staff_list:
                        print("❌ No staff found. Create some staff first.")
                        return
                    
                    # Find staff with PINs
                    staff_with_pins = [s for s in staff_list if s.get('clock_pin')]
                    
                    if not staff_with_pins:
                        print("❌ No staff have PINs. This might indicate PIN generation failed.")
                        return
                    
                    print(f"✅ Found {len(staff_with_pins)} staff with PINs")
                    test_staff = staff_with_pins[0]
                    test_pin = test_staff['clock_pin']
                    print(f"🔑 Testing with PIN: {test_pin} for {test_staff['first_name']} {test_staff['last_name']}")
                else:
                    print(f"❌ Failed to fetch staff: {response.status}")
                    return
            
            # Test 1: Clock In with valid PIN
            print("\n🔄 Test 1: Clock In with valid PIN")
            clock_in_data = {
                "pin": test_pin,
                "action": "clock_in",
                "notes": "Test clock in via PIN"
            }
            
            async with session.post(f"{base_url}/api/attendance/quick-attendance", 
                                   json=clock_in_data) as response:
                result = await response.json()
                print(f"Status: {response.status}")
                print(f"Response: {json.dumps(result, indent=2, default=str)}")
                
                if result.get('success'):
                    print("✅ Clock in successful")
                else:
                    print(f"❌ Clock in failed: {result.get('message')}")
            
            # Wait a moment
            await asyncio.sleep(2)
            
            # Test 2: Try to clock in again (should fail)
            print("\n🔄 Test 2: Try to clock in again (should fail)")
            async with session.post(f"{base_url}/api/attendance/quick-attendance", 
                                   json=clock_in_data) as response:
                result = await response.json()
                print(f"Status: {response.status}")
                print(f"Response: {json.dumps(result, indent=2, default=str)}")
                
                if not result.get('success'):
                    print("✅ Duplicate clock-in properly prevented")
                else:
                    print("❌ Duplicate clock-in should have been prevented")
            
            # Test 3: Clock out
            print("\n🔄 Test 3: Clock Out")
            clock_out_data = {
                "pin": test_pin,
                "action": "clock_out",
                "notes": "Test clock out via PIN"
            }
            
            async with session.post(f"{base_url}/api/attendance/quick-attendance", 
                                   json=clock_out_data) as response:
                result = await response.json()
                print(f"Status: {response.status}")
                print(f"Response: {json.dumps(result, indent=2, default=str)}")
                
                if result.get('success'):
                    print(f"✅ Clock out successful. Hours worked: {result.get('hours_worked', 'N/A')}")
                else:
                    print(f"❌ Clock out failed: {result.get('message')}")
            
            # Test 4: Invalid PIN
            print("\n🔄 Test 4: Invalid PIN")
            invalid_data = {
                "pin": "9999",
                "action": "clock_in",
                "notes": "Test with invalid PIN"
            }
            
            async with session.post(f"{base_url}/api/attendance/quick-attendance", 
                                   json=invalid_data) as response:
                result = await response.json()
                print(f"Status: {response.status}")
                print(f"Response: {json.dumps(result, indent=2, default=str)}")
                
                if not result.get('success'):
                    print("✅ Invalid PIN properly rejected")
                else:
                    print("❌ Invalid PIN should have been rejected")
            
            # Test 5: Invalid action
            print("\n🔄 Test 5: Invalid action")
            invalid_action_data = {
                "pin": test_pin,
                "action": "invalid_action",
                "notes": "Test with invalid action"
            }
            
            async with session.post(f"{base_url}/api/attendance/quick-attendance", 
                                   json=invalid_action_data) as response:
                result = await response.json()
                print(f"Status: {response.status}")
                print(f"Response: {json.dumps(result, indent=2, default=str)}")
                
                if not result.get('success'):
                    print("✅ Invalid action properly rejected")
                else:
                    print("❌ Invalid action should have been rejected")
            
            print("\n🎉 PIN-based attendance testing completed!")
            
        except Exception as e:
            print(f"❌ Test failed with error: {str(e)}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_pin_attendance())
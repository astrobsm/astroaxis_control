import requests
import json

# Test admin login
print("Testing admin login...")
response = requests.post(
    "http://127.0.0.1:8004/api/auth/login",
    json={
        "email": "admin@astroasix.com",
        "password": "Admin123!"
    }
)

print(f"Status Code: {response.status_code}")
print(f"Response: {json.dumps(response.json(), indent=2)}")

if response.status_code == 200:
    print("\n✅ Admin login successful!")
    data = response.json()
    print(f"Token: {data['access_token'][:50]}...")
    print(f"User: {data['user']['full_name']} ({data['user']['role']})")
else:
    print(f"\n❌ Login failed: {response.json()}")

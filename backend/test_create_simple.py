import requests
import time

sku = f'FINAL-TEST-{int(time.time())}'
payload = {
    'sku': sku,
    'name': 'Final Test Product',
    'unit': 'each',
    'pricing': [
        {
            'unit': 'each',
            'cost_price': 10,
            'retail_price': 15,
            'wholesale_price': 12
        },
        {
            'unit': 'box',
            'cost_price': 90,
            'retail_price': 135,
            'wholesale_price': 112
        }
    ]
}

print(f"Creating product with SKU: {sku}")
r = requests.post('http://localhost:8004/api/products/', json=payload)
print(f"Status: {r.status_code}")

if r.ok:
    data = r.json()
    print(f"Success: {data.get('success')}")
    print(f"Message: {data.get('message')}")
    print(f"Data: {data.get('data')}")
    
    if data.get('data'):
        product_data = data['data']
        print(f"\nProduct ID: {product_data.get('id')}")
        print(f"Pricing entries: {len(product_data.get('pricing', []))}")
        for p in product_data.get('pricing', []):
            print(f"  - Unit: {p.get('unit')}, Retail: {p.get('retail_price')}")
else:
    print(f"Error: {r.text}")

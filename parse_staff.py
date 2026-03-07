import json, sys

data = json.load(sys.stdin)
items = data if isinstance(data, list) else data.get('items', data.get('staff', []))
targets = ['BSM9697','BSM6843','BSM4754','BSM0507','BSM0629','BSM5193','BSM2803','BSM1529','BSM4947','BSM9729']
for s in items:
    eid = s.get('employee_id', '')
    if eid in targets:
        print(f"{eid}|{s['id']}|{s.get('payment_mode','')}|{s.get('hourly_rate',0)}|{s.get('monthly_salary',0)}")

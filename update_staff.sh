#!/bin/bash
IDS=(
  "05b31239-ed30-4ff9-9f51-62b6337741b8"
  "b1ddb70a-7f3e-4982-b8b5-162f8f5c87d0"
  "3d47edfb-d45b-4571-9d27-a3883be4b528"
  "66295f4c-4b7d-4923-94bc-a9462439e1bf"
  "7b25a64d-a274-4af6-a65b-af7e420de43e"
  "ab43ae57-b31a-47a9-8fec-e6efc9af6bd2"
  "b8814a20-8de7-4a9f-a3aa-71259b827543"
  "d72447f8-25e4-4ab6-8f24-ba5129df741c"
  "df870b6f-b987-4552-bf90-2a49ba8a47f8"
  "3016646e-1798-410f-8f0c-be080152412f"
)

for id in "${IDS[@]}"; do
  result=$(curl -s -X PUT -H "Content-Type: application/json" \
    -d '{"payment_mode":"hourly","hourly_rate":425}' \
    "http://localhost:8004/api/staff/staffs/$id")
  echo "$id => $result" | head -c 200
  echo ""
done

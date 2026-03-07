$ids = @(
    "05b31239-ed30-4ff9-9f51-62b6337741b8",  # BSM9697 Nkiru Chidebe
    "b1ddb70a-7f3e-4982-b8b5-162f8f5c87d0",  # BSM6843 Nkiruka Arua
    "3d47edfb-d45b-4571-9d27-a3883be4b528",  # BSM4754 Chinelo Vivian Enioka
    "66295f4c-4b7d-4923-94bc-a9462439e1bf",  # BSM0507 Chibuzo Daniel Amadi
    "7b25a64d-a274-4af6-a65b-af7e420de43e",  # BSM0629 Calista Amadi
    "ab43ae57-b31a-47a9-8fec-e6efc9af6bd2",  # BSM5193 Uzoamaka Ibeh
    "b8814a20-8de7-4a9f-a3aa-71259b827543",  # BSM2803 Ugochukwu Victor Onyebuch
    "d72447f8-25e4-4ab6-8f24-ba5129df741c",  # BSM1529 Julieth Ozioko
    "df870b6f-b987-4552-bf90-2a49ba8a47f8",  # BSM4947 Chizoba MaryRose Chukwueke
    "3016646e-1798-410f-8f0c-be080152412f"   # BSM9729 Arinze Udegbunam
)

foreach ($id in $ids) {
    $body = '{"payment_mode":"hourly","hourly_rate":425}'
    $result = ssh -i ~/.ssh/id_rsa -o StrictHostKeyChecking=no root@159.89.29.45 "curl -s -X PUT -H 'Content-Type: application/json' -d '$body' http://localhost:8004/api/staff/staffs/$id"
    Write-Host "$id => $result"
}

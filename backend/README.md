Backend README

To run tests locally (without Docker):

1. Create a virtualenv and install requirements.txt
2. Ensure PostgreSQL running and DATABASE_URL points to it, or modify DATABASE_URL in app/db.py to point to local DB.
3. Run the seed script and tests:

```powershell
python -m backend.scripts.seed_data
pytest -q
```


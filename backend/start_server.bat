@echo off
cd /d C:\Users\USER\ASTROAXIS\backend
set PYTHONPATH=
echo Starting ASTRO-ASIX Backend Server...
python -m uvicorn app.main:app --host 127.0.0.1 --port 8004 --reload

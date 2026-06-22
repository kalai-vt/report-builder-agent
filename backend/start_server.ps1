Set-Location "d:\Report_builder\report-builder-agent\backend"
python -m uvicorn main:app --host 0.0.0.0 --port 8080

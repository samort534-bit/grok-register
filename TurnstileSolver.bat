@echo off
python -c "import quart, camoufox, patchright" 2>nul || pip install -r requirements.txt
python api_solver.py --browser_type camoufox --thread 5 --debug

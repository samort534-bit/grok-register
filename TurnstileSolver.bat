@echo off
pip install -r requirements.txt >nul 2>&1
python api_solver.py --browser_type camoufox --thread 5 --debug

@echo off
cd /d F:\LangChain\Interview\OCR
"F:\LangChain\pyversion\python.exe" -m streamlit run streamlit_app.py --server.port 8501 --server.headless true > outputs\streamlit.stdout.log 2> outputs\streamlit.stderr.log

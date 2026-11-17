.PHONY: install test app
install:
	uv pip sync requirements.lock
test:
	python -m pytest -q
app:
	python -m streamlit run app.py

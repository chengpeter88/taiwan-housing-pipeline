.PHONY: install test dbt-build verify
install:
	uv pip sync requirements.lock
test:
	python -m pytest -q
dbt-build:
	dbt build --project-dir analytics_bigquery --profiles-dir analytics_bigquery
verify:
	python -m scripts.verify_bigquery

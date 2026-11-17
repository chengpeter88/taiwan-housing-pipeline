.PHONY: install test dbt-build
install:
	uv pip sync requirements.lock
test:
	python -m pytest -q
dbt-build:
	dbt build --project-dir analytics_bigquery --profiles-dir analytics_bigquery

.PHONY: install test lint clean build run

install:
	uv sync

test:
	pytest --cov=src tests/ -v

lint:
	flake8 src/ tests/
	mypy src/ --ignore-missing-imports

clean:
	rm -rf build/ dist/ *.egg-info/ __pycache__/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

build:
	uv build

run:
	uvicorn src.api.server:create_app --reload --host 0.0.0.0 --port 8000

docker-build:
	docker compose -f infra/docker-compose.yml build

docker-up:
	docker compose -f infra/docker-compose.yml up -d

docker-down:
	docker compose -f infra/docker-compose.yml down

# 2019-01-15T19:25:56 update

# 2019-01-24T16:02:28 update

# 2019-02-13T18:06:36 update

# 2019-02-27T19:36:20 update

# 2019-03-04T18:04:38 update

# 2019-03-08T08:54:54 update

# 2019-03-20T08:37:37 update

# 2019-03-27T13:49:16 update

# 2019-06-02T17:36:51 update

# 2019-06-27T08:27:22 update

# 2019-07-10T08:43:44 update

# 2019-10-29T15:35:38 update

# 2019-11-22T14:55:42 update

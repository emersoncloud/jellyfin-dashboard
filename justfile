set dotenv-load := true

host := env("UNRAID_HOST", "")
remote_dir := "/mnt/user/appdata/jellyfin-dashboard"

default:
    @just --list

# Run exporter unit tests
test:
    cd exporter && uv run --group dev pytest -q

# Hit the real Jellyfin API from this machine (.env's JELLYFIN_URL is the in-container address)
check-api url=env("JELLYFIN_REMOTE_URL", ""):
    @test -n "{{url}}" || (echo "set JELLYFIN_REMOTE_URL in .env" && exit 1)
    cd exporter && uv run python -c 'from exporter import *; import os; c = JellyfinCollector("{{url}}", os.environ["JELLYFIN_API_KEY"]); [print(s) for s in map(parse_session, c.fetch_sessions()) if s] or print("no active streams (API OK)")'

# Regenerate grafana/dashboards/jellyfin.json from tools/gen_dashboard.py
dashboard:
    python3 tools/gen_dashboard.py grafana/dashboards/jellyfin.json

# Run the whole stack locally against the real server
up:
    docker compose up -d --build

down:
    docker compose down

# Copy to Unraid and (re)start the stack. Requires SSH enabled + Compose Manager plugin.
deploy:
    test -f .env || (echo "missing .env — copy .env.example" && exit 1)
    @test -n "{{host}}" || (echo "set UNRAID_HOST in .env" && exit 1)
    ssh {{host}} 'mkdir -p {{remote_dir}} && docker compose version >/dev/null'
    rsync -av --delete --exclude .git --exclude .venv --exclude __pycache__ --exclude .pytest_cache --exclude '*.egg-info' ./ {{host}}:{{remote_dir}}/
    ssh {{host}} 'cd {{remote_dir}} && docker compose up -d --build --remove-orphans && docker compose ps'

logs service="jellyfin-exporter":
    ssh {{host}} 'cd {{remote_dir}} && docker compose logs --tail 50 -f {{service}}'

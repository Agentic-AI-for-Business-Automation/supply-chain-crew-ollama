"""Find the container behind a docker compose service, whatever its container_name or override is."""
import os, subprocess

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))


def container(service: str, env_var: str, fallback: str) -> str:
    """env_var (explicit) > the compose service of THIS project (docker compose ps) > fallback name."""
    if os.getenv(env_var):
        return os.environ[env_var]
    r = subprocess.run(["docker", "compose", "ps", "-a", "-q", service], cwd=ROOT, capture_output=True, text=True)
    ids = r.stdout.split()
    return ids[0] if ids else fallback

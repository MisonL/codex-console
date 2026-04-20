from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_production_deploy_script_uses_canonical_project_directory():
    script = ROOT / "scripts" / "docker" / "deploy-production.sh"
    content = script.read_text(encoding="utf-8")

    assert "Path(sys.argv[1]).resolve()" in content
    assert "cygpath -m" in content
    assert "--project-directory" in content
    assert "docker compose" in content
    assert "WEBUI_ACCESS_PASSWORD" in content
    assert "review-pass" not in content
    assert "admin123" not in content


def test_production_deploy_script_has_valid_bash_syntax():
    script = ROOT / "scripts" / "docker" / "deploy-production.sh"

    result = subprocess.run(
        ["bash", "-n", str(script)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_readme_documents_safe_compose_entrypoint():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "bash scripts/docker/deploy-production.sh" in readme
    assert (
        'docker compose --project-directory "$(pwd -P)" -f docker-compose.yml up -d --build'
        in readme
    )
    assert "不要裸跑 `docker compose up`" in readme
    assert "Windows Git Bash" in readme

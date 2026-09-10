from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]

DEPLOY_SCRIPTS = (
    "scripts/deploy_cloud_run_dev.sh",
    "scripts/deploy_cloud_run_staging.sh",
    "scripts/deploy_ui_dev.sh",
    "scripts/deploy_ui_staging.sh",
)

RUNTIME_LAUNCHERS = (
    "api/ai_impact.py",
    "api/content_quality_jobs.py",
    "api/crawl_jobs.py",
    "api/export_jobs.py",
    "api/prompt_jobs.py",
    "api/sentiment_jobs.py",
    "api/topic_content_jobs.py",
)


@pytest.mark.parametrize("relative_path", DEPLOY_SCRIPTS)
def test_deploy_scripts_use_migrated_project_and_iap(relative_path: str) -> None:
    script = (ROOT / relative_path).read_text(encoding="utf-8")

    assert 'PROJECT="${GCP_PROJECT:-geo-tool-emea-ds}"' in script
    assert "--iap" in script
    assert "--allow-unauthenticated" not in script
    assert "_add_secret AUTH_CLIENT_ID google-oauth-client-id-geo-tool 1" in script
    assert "_add_secret AUTH_CLIENT_SECRET google-oauth-client-secret-geo-tool 1" in script
    assert "_add_secret AUTH_COOKIE_SECRET auth-cookie-secret-geo-tool 1" in script
    assert "GOOGLE_OAUTH_DOMAIN" in script
    assert "IAP_HOSTED_DOMAIN" in script
    assert "GSC_OAUTH_REDIRECT_URI=${SERVICE_URL}/api/gsc/callback" in script


@pytest.mark.parametrize("relative_path", RUNTIME_LAUNCHERS)
def test_runtime_launchers_have_no_sandbox_project_fallback(relative_path: str) -> None:
    source = (ROOT / relative_path).read_text(encoding="utf-8")
    assert "emea-ds-" + "sandbox" not in source


@pytest.mark.parametrize(
    "relative_path",
    ("scripts/deploy_ui_dev.sh", "scripts/deploy_ui_staging.sh"),
)
def test_ui_only_deploys_preserve_runtime_configuration(relative_path: str) -> None:
    script = (ROOT / relative_path).read_text(encoding="utf-8")
    assert '--update-env-vars="${ENV_VARS}"' in script
    assert '--set-env-vars="${ENV_VARS}"' not in script
    assert "update-secrets" in script
    assert "GSC_OAUTH_REDIRECT_URI=${SERVICE_URL}/api/gsc/callback" in script


@pytest.mark.parametrize("relative_path", (".gcloudignore", ".dockerignore"))
def test_cloud_build_context_excludes_local_credentials(relative_path: str) -> None:
    patterns = (ROOT / relative_path).read_text(encoding="utf-8").splitlines()
    assert ".streamlit/client_secret.json" in patterns
    assert "local-auth/" in patterns
    assert "*credentials*.json" in patterns

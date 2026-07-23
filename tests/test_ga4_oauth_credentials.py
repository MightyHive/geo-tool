from __future__ import annotations

import ga4_oauth


def test_missing_expiry_forces_oauth_token_refresh() -> None:
    class StaleSessionCredential:
        valid = True
        refresh_token = "refresh-token"
        expiry = None
        refreshed = False

        def refresh(self, _request) -> None:
            self.refreshed = True

    credential = StaleSessionCredential()

    result = ga4_oauth.ensure_fresh_credentials(credential)

    assert result is credential
    assert credential.refreshed is True

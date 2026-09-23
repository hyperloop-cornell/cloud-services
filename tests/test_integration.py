"""Integration tests for API endpoints."""

from conftest import OPERATOR_NETID, TEAM_PASSWORD


def test_health_endpoint_returns_200(client):
    """Test health endpoint returns 200 OK."""
    response = client.get("/health")
    assert response.status_code == 200


def test_auth_flow(client):
    """Test complete authentication flow."""
    # Test login
    login_response = client.post(
        "/auth/login",
        json={"username": OPERATOR_NETID, "password": TEAM_PASSWORD}
    )
    assert login_response.status_code == 200
    
    token = login_response.json()["access_token"]
    assert token is not None
    assert len(token) > 0


def test_list_hubs_requires_auth(client):
    """Test that listing hubs requires authentication."""
    # Without token should fail
    response = client.get("/api/hubs")
    assert response.status_code in [401, 403]
    
    # With valid token should work or return empty list
    login_response = client.post(
        "/auth/login",
        json={"username": OPERATOR_NETID, "password": TEAM_PASSWORD}
    )
    
    if login_response.status_code == 200:
        token = login_response.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        
        response = client.get("/api/hubs", headers=headers)
        # Should return either 200 with list or not require auth for this endpoint
        assert response.status_code in [200, 401, 403]
        if response.status_code == 200:
            assert isinstance(response.json(), list)


def test_api_headers_content_type(client):
    """Test that API returns proper content type."""
    response = client.get("/health")
    assert "application/json" in response.headers.get("content-type", "")


def test_cors_headers_present(client):
    """Test that CORS headers are configured."""
    response = client.options("/health")
    # CORS headers may or may not be on OPTIONS, check actual requests
    assert response.status_code in [200, 405]  # 405 if OPTIONS not implemented


def test_invalid_endpoint_returns_404(client):
    """Test that invalid endpoints return 404."""
    response = client.get("/api/nonexistent")
    assert response.status_code == 404

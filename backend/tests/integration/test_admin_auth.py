"""Admin sign-in protects Settings; password can be changed."""

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app

EMAIL = get_settings().admin_email


def login(c, password="georisk-admin", email=EMAIL):
    return c.post("/api/v1/auth/login", json={"email": email, "password": password})


def test_login_settings_and_password_change(db):
    c = TestClient(app)
    assert login(c, "wrong").status_code == 401
    assert login(c, email="someone@else.com").status_code == 401
    assert c.put("/api/v1/settings/alert_threshold_level", json={"value": "high"}).status_code == 401
    assert c.put("/api/v1/settings/alert_threshold_level", json={"value": "high"},
                 headers={"Authorization": "Bearer nope|1|x"}).status_code == 401

    r = login(c, email=EMAIL.upper())  # email is case-insensitive
    assert r.status_code == 200
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    assert c.get("/api/v1/auth/me", headers=h).json()["email"] == EMAIL
    assert c.put("/api/v1/settings/alert_threshold_level", json={"value": "high"}, headers=h).status_code == 200

    assert c.post("/api/v1/auth/change-password", json={"current_password": "bad", "new_password": "a-new-pass1"},
                  headers=h).status_code == 400
    assert c.post("/api/v1/auth/change-password", json={"current_password": "georisk-admin",
                                                        "new_password": "a-new-pass1"}, headers=h).status_code == 200
    assert c.get("/api/v1/auth/me", headers=h).status_code == 401  # old sessions are signed out
    assert login(c).status_code == 401 and login(c, "a-new-pass1").status_code == 200

"""Register, sign in, and the gate on the todo routes.

The board is shared, so these verify access control — not per-user data
scoping, which this project deliberately does not do.
"""

CREDS = {"username": "ada", "password": "correct-horse-battery"}


async def register(anon_client, **overrides):
    return await anon_client.post("/api/auth/register", json={**CREDS, **overrides})


async def test_register_returns_a_token_and_the_user(anon_client):
    response = await register(anon_client)
    assert response.status_code == 201, response.text

    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["expires_in"] > 0
    assert body["user"]["username"] == "ada"
    assert "password" not in str(body)
    assert "password_hash" not in str(body)


async def test_register_rejects_a_duplicate_username(anon_client):
    await register(anon_client)
    response = await register(anon_client)
    assert response.status_code == 409
    assert response.json()["code"] == "USERNAME_TAKEN"


async def test_usernames_are_case_insensitive(anon_client):
    """Without the functional unique index, "Ada" and "ada" would both register
    and the case-insensitive lookup would be ambiguous."""
    await register(anon_client)
    response = await register(anon_client, username="Ada")
    assert response.status_code == 409


async def test_login_with_the_right_password_returns_a_token(anon_client):
    await register(anon_client)
    response = await anon_client.post("/api/auth/login", json=CREDS)
    assert response.status_code == 200
    assert response.json()["access_token"]


async def test_login_is_case_insensitive_on_username(anon_client):
    await register(anon_client)
    response = await anon_client.post("/api/auth/login", json={**CREDS, "username": "ADA"})
    assert response.status_code == 200


async def test_login_with_a_wrong_password_is_rejected(anon_client):
    await register(anon_client)
    response = await anon_client.post("/api/auth/login", json={**CREDS, "password": "wrong-password"})
    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_CREDENTIALS"


async def test_unknown_user_and_wrong_password_are_indistinguishable(anon_client):
    """The response must not reveal whether an account exists."""
    await register(anon_client)
    wrong_pw = await anon_client.post("/api/auth/login", json={**CREDS, "password": "wrong-password"})
    no_user = await anon_client.post(
        "/api/auth/login", json={"username": "nobody", "password": "wrong-password"}
    )
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json()["code"] == no_user.json()["code"]
    assert wrong_pw.json()["detail"] == no_user.json()["detail"]


async def test_register_rejects_a_short_password(anon_client):
    response = await register(anon_client, password="short")
    assert response.status_code == 422


async def test_register_rejects_an_invalid_username(anon_client):
    response = await register(anon_client, username="has spaces")
    assert response.status_code == 422


async def test_me_returns_the_signed_in_user(anon_client):
    token = (await register(anon_client)).json()["access_token"]
    response = await anon_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["username"] == "ada"


async def test_todos_require_authentication(anon_client):
    response = await anon_client.get("/api/todos")
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


async def test_todos_are_reachable_with_a_token(anon_client):
    token = (await register(anon_client)).json()["access_token"]
    response = await anon_client.get("/api/todos", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200


async def test_a_garbage_token_is_rejected(anon_client):
    response = await anon_client.get("/api/todos", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401


async def test_a_non_bearer_scheme_is_rejected(anon_client):
    token = (await register(anon_client)).json()["access_token"]
    response = await anon_client.get("/api/todos", headers={"Authorization": f"Basic {token}"})
    assert response.status_code == 401


async def test_the_board_is_shared_between_accounts(anon_client):
    """Two accounts, one list — this is the whole ownership decision."""
    ada = (await register(anon_client)).json()["access_token"]
    grace = (
        await register(anon_client, username="grace", password="another-good-password")
    ).json()["access_token"]

    created = await anon_client.post(
        "/api/todos", json={"name": "Shared item"}, headers={"Authorization": f"Bearer {ada}"}
    )
    assert created.status_code == 201

    seen = await anon_client.get(
        f"/api/todos/{created.json()['id']}", headers={"Authorization": f"Bearer {grace}"}
    )
    assert seen.status_code == 200
    assert seen.json()["name"] == "Shared item"

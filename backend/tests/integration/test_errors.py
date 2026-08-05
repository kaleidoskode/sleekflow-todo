import httpx
import pytest
from pydantic import BaseModel

from app.errors import BlockedByDependencies, NotFound
from app.main import create_app


def build_probe_app():
    """The real app factory plus throwaway routes that raise each error shape."""
    app = create_app()

    class Body(BaseModel):
        name: str

    @app.get("/_probe/not-found")
    async def raise_not_found():
        raise NotFound("No todo with id 42.")

    @app.get("/_probe/blocked")
    async def raise_blocked():
        raise BlockedByDependencies("Cannot start.", extra={"unmet_dependency_count": 3})

    @app.post("/_probe/validated")
    async def validated(body: Body):
        return {"ok": True}

    return app


@pytest.fixture
async def probe_client():
    transport = httpx.ASGITransport(app=build_probe_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_domain_error_becomes_problem_details(probe_client):
    response = await probe_client.get("/_probe/not-found")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["code"] == "NOT_FOUND"
    assert body["status"] == 404
    assert body["title"]
    assert body["detail"] == "No todo with id 42."


async def test_domain_error_extra_is_spread_into_the_body(probe_client):
    """`extra` carries the machine-readable payload clients act on."""
    response = await probe_client.get("/_probe/blocked")
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "BLOCKED_BY_DEPENDENCIES"
    assert body["unmet_dependency_count"] == 3


async def test_validation_failure_becomes_problem_details(probe_client):
    response = await probe_client.post("/_probe/validated", json={})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert [e["field"] for e in body["errors"]] == ["name"]

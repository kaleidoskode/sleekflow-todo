import httpx
import pytest
from pydantic import BaseModel

from app.core.errors import BlockedByDependencies, NotFound
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
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert [e["field"] for e in body["errors"]] == ["name"]


class TestEveryErrorIsProblemDetails:
    """The API documents RFC 9457 throughout, so nothing may answer in a
    different shape — including failures the framework raises rather than the
    domain. A client that learned one error shape should never meet a second.
    """

    async def test_unknown_route(self, anon_client):
        response = await anon_client.get("/api/definitely-not-here")
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["code"] == "NOT_FOUND"

    async def test_wrong_method(self, anon_client):
        response = await anon_client.put("/health")
        assert response.status_code == 405
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["code"] == "METHOD_NOT_ALLOWED"

    async def test_every_error_body_carries_the_same_keys(self, anon_client):
        """type/title/status/detail/code on all of them, whatever raised it."""
        responses = [
            await anon_client.get("/api/definitely-not-here"),          # framework
            await anon_client.get("/api/todos"),                         # domain (401)
            await anon_client.post("/api/auth/register", json={}),       # validation
        ]
        for response in responses:
            body = response.json()
            assert {"type", "title", "status", "detail", "code"} <= set(body), body
            assert body["status"] == response.status_code


class TestOpenAPIIsWellFormed:
    """The schema is a deliverable: reviewers read it, and clients generate from
    it. Nothing here is caught by exercising the API itself."""

    def test_component_keys_match_the_spec_pattern(self):
        """OpenAPI requires component keys to match ^[a-zA-Z0-9._-]+$.

        Swagger UI renders an invalid key without complaint, so a space in a
        securityScheme name survives every manual check and only surfaces in a
        strict validator or a generated client.
        """
        import re

        from app.main import app

        pattern = re.compile(r"^[a-zA-Z0-9._-]+$")
        offenders = [
            f"components.{section}.{key!r}"
            for section, items in app.openapi().get("components", {}).items()
            for key in items
            if not pattern.match(key)
        ]
        assert offenders == [], offenders

    def test_every_gated_route_references_a_declared_scheme(self):
        """A security requirement naming a scheme that does not exist is not an
        error anywhere — it just silently documents nothing."""
        from app.main import app

        schema = app.openapi()
        declared = set(schema["components"]["securitySchemes"])
        for path, item in schema["paths"].items():
            for method, operation in item.items():
                for requirement in operation.get("security", []):
                    unknown = set(requirement) - declared
                    assert not unknown, f"{method.upper()} {path} -> {unknown}"

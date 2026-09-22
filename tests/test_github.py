import json

import pytest
import responses
from github import GithubException

from tools.github import GitHub

API = "https://api.github.com/repos/example/repo"
WIRE_API = API.replace("github.com/", "github.com:443/")
SHA = "a" * 40


def wire_url(url):
    return url.replace("github.com/", "github.com:443/")


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr("github.Requester.time.sleep", lambda _: None)
    client = GitHub("example/repo")
    yield client
    client.client.close()


@pytest.fixture
def http():
    with responses.RequestsMock() as mock:
        yield mock


@pytest.fixture
def release():
    return {
        "id": 42,
        "url": f"{API}/releases/42",
        "assets_url": f"{API}/releases/42/assets",
        "upload_url": "https://uploads.github.com/repos/example/repo/releases/42/assets{?name,label}",
        "tag_name": "nightly",
        "name": "Previous nightly",
        "body": "Previous successful generation",
        "draft": False,
        "prerelease": True,
    }


def test_commit_resolves_ref_to_immutable_sha(api, http):
    http.get(f"{WIRE_API}/commits/refs%2Ftags%2Fv5.048", json={"sha": SHA})
    assert api.commit("example/repo", "refs/tags/v5.048") == SHA


def test_create_release_stays_draft(api, http, release):
    http.post(f"{WIRE_API}/releases", json=release, status=201)
    assert api.create_release("nightly", SHA, True) == release
    body = json.loads(http.calls[0].request.body)
    assert body["draft"] and body["prerelease"]
    assert body["target_commitish"] == SHA
    assert body["make_latest"] == "false"


def test_draft_update_preserves_other_release_fields(api, http, release):
    http.patch(wire_url(release["url"]), json={**release, "draft": True})
    updated = api.update_release(release, draft=True)
    assert updated["draft"] is True
    body = json.loads(http.calls[0].request.body)
    for field in ("tag_name", "name", "body", "prerelease"):
        assert body[field] == release[field]


def test_upload_clobbers_only_same_name_asset(api, http, release, tmp_path):
    path = tmp_path / "new.tar.gz"
    path.write_bytes(b"\x1f\x8b\x00\xff")
    old = {"id": 1, "name": "old.tar.gz", "url": f"{API}/releases/assets/1"}
    existing = {"id": 2, "name": path.name, "url": f"{API}/releases/assets/2"}
    http.get(wire_url(release["assets_url"]), json=[old, existing])
    http.delete(wire_url(existing["url"]), status=204)
    http.post(wire_url(release["upload_url"].split("{")[0]), json={"id": 3}, status=201)
    api.upload(release, [path])
    assert [call.request.method for call in http.calls] == ["GET", "DELETE", "POST"]
    assert http.calls[-1].request.body == path.read_bytes()
    assert "name=new.tar.gz" in http.calls[-1].request.url


def test_upload_failure_stops_after_first_asset(api, http, release, tmp_path):
    paths = [tmp_path / name for name in ("first.tar.gz", "second.tar.gz")]
    for path in paths:
        path.write_bytes(b"archive")
    http.get(wire_url(release["assets_url"]), json=[])
    http.post(
        wire_url(release["upload_url"].split("{")[0]),
        json={"message": "upload failed"},
        status=500,
    )
    with pytest.raises(GithubException):
        api.upload(release, paths)
    assert [call.request.method for call in http.calls] == ["GET", "POST"]


def test_nightly_tag_points_to_packaging_commit(api, http):
    http.get(f"{WIRE_API}/git/ref/tags/nightly", json={"url": f"{API}/git/refs/tags/nightly"})
    http.patch(f"{WIRE_API}/git/refs/tags/nightly", json={"ref": "refs/tags/nightly"})
    api.update_tag("nightly", SHA)
    assert json.loads(http.calls[-1].request.body) == {"sha": SHA, "force": True}

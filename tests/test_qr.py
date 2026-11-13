import pytest

from services.qr import MAX_TEXT_LENGTH, render_svg

URL = "/api/v1/qr.svg"


def test_the_svg_is_a_self_contained_vector_image():
    svg = render_svg("http://localhost:8000/?code=ABC234").decode()
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert 'xmlns="http://www.w3.org/2000/svg"' in svg
    assert "http://localhost" not in svg.replace('xmlns="http://www.w3.org/2000/svg"', "")
    assert "<script" not in svg and "href" not in svg


def test_the_same_text_gives_the_same_image_and_different_text_a_different_one():
    first = render_svg("https://example.org/?code=AAAAAA")
    assert first == render_svg("https://example.org/?code=AAAAAA")
    assert first != render_svg("https://example.org/?code=BBBBBB")


def test_longer_text_makes_a_bigger_symbol():
    short = render_svg("a")
    long = render_svg("a" * MAX_TEXT_LENGTH)
    width = lambda svg: int(svg.decode().split('width="')[1].split('"')[0])  # noqa: E731
    assert width(long) > width(short)


@pytest.mark.asyncio
async def test_the_endpoint_needs_no_login_and_returns_an_svg(client):
    response = await client.get(URL, params={"text": "http://testserver/?code=ABC234"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert "max-age" in response.headers["cache-control"]
    assert response.content.startswith(b"<svg")


@pytest.mark.asyncio
async def test_empty_missing_and_oversized_text_are_refused(client):
    assert (await client.get(URL)).status_code == 422
    assert (await client.get(URL, params={"text": ""})).status_code == 422
    too_long = await client.get(URL, params={"text": "x" * (MAX_TEXT_LENGTH + 1)})
    assert too_long.status_code == 422
    longest = await client.get(URL, params={"text": "x" * MAX_TEXT_LENGTH})
    assert longest.status_code == 200


@pytest.mark.asyncio
async def test_unicode_text_is_encoded(client):
    response = await client.get(URL, params={"text": "Привет, код 123456"})
    assert response.status_code == 200 and response.content.startswith(b"<svg")


def test_the_module_makes_no_network_calls():
    import services.qr as module

    source = open(module.__file__, encoding="utf-8").read()
    for word in ("httpx", "requests", "urllib", "socket", "aiohttp"):
        assert word not in source

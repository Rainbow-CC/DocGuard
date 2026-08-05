from docguard.services.vision import VisionResponse, VisionResponseCache


class FakeVisionAdapter:
    adapter_id = "fake"
    model = "fake-v1"
    calls = 0

    def describe(self, image: bytes, prompt: str, *, media_type: str = "image/png") -> VisionResponse:
        self.calls += 1
        return VisionResponse(self.adapter_id, self.model, '{"content":"{}"}')


def test_visual_cache_keys_on_image_prompt_adapter_and_model(tmp_path) -> None:
    cache = VisionResponseCache(tmp_path / "vision.sqlite3")
    adapter = FakeVisionAdapter()

    first, first_hit = cache.get_or_create(b"png", "prompt", adapter)
    second, second_hit = cache.get_or_create(b"png", "prompt", adapter)
    cache.get_or_create(b"png", "changed prompt", adapter)

    assert first.raw_response == second.raw_response
    assert first_hit is False
    assert second_hit is True
    assert adapter.calls == 2

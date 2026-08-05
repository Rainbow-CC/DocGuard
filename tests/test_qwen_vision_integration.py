"""Opt-in live test for the configured Alibaba Qwen vision endpoint."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from docguard.services.vision import QwenVisionAdapter


pytestmark = pytest.mark.integration


@pytest.mark.skipif(not os.getenv("DASHSCOPE_API_KEY"), reason="requires DASHSCOPE_API_KEY")
def test_qwen_describes_saved_capybara_image() -> None:
    image = (Path(__file__).parent / "fixtures" / "capybara-with-hat.jpg").read_bytes()

    response = QwenVisionAdapter().describe(image, "图片里描绘了什么？", media_type="image/jpeg")
    print(response.raw_response)

    assert response.raw_response
    assert "content" in response.raw_response

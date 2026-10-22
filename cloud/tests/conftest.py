"""The tests never reach the network: the GitHub collector's background task stays off
unless a test turns it on with a mocked transport (`Settings(github_collect=True)`)."""

from __future__ import annotations

import json
import os

import pytest

os.environ["GITHUB_COLLECT"] = "0"
# Most relay tests exercise the optional self-hosted media/pricing machinery. Production
# defaults are tested separately with these overrides removed.
os.environ["CLOUD_MEDIA_ENABLED"] = "1"
os.environ["CLOUD_MODELS"] = json.dumps(
    [
        {
            "id": "deepseek-v4.1-flash",
            "name": "DeepSeek V4.1 Flash",
            "upstream": "deepseek-v4.1-flash",
            "input_modalities": ["text", "image"],
            "for": ["chat"],
            "recommended": True,
            "recommended_for": ["chat"],
            "in_mult": 0.7,
            "out_mult": 0.7,
            "price_in": 2.0,
            "price_out": 8.0,
            "price_in_idle": 1.0,
            "price_out_idle": 4.0,
            "cached_in_rate": 0.1,
        },
        {
            "id": "qwen3.8-27b",
            "name": "Qwen 3.8 27B",
            "upstream": "qwen3.8-27b",
            "input_modalities": ["text", "image"],
            "for": ["gui", "chat"],
            "recommended": True,
            "recommended_for": ["gui"],
            "price_in": 3.0,
            "price_out": 12.0,
            "cached_in_rate": 0.2,
        },
        {
            "id": "qwen3.8-flash",
            "name": "Qwen 3.8 Flash",
            "upstream": "qwen3.8-flash",
            "input_modalities": ["text", "image"],
            "in_mult": 0.3,
            "out_mult": 0.3,
            "price_in": 0.8,
            "price_out": 2.7,
            "cached_in_rate": 0.2,
        },
        {
            "id": "qwen-image-3.0",
            "name": "Qwen Image 3.0",
            "upstream": "qwen-image-3.0",
            "kind": "image",
            "output_modalities": ["image"],
            "per_image": 30000,
            "recommended": True,
            "price_image": 0.18,
            "price_image_2k": 0.18,
            "price_image_in": 0.02,
        },
        {
            "id": "wan2.2-i2v-flash",
            "name": "Wan 2.2 Flash (video)",
            "upstream": "wan2.2-i2v-flash",
            "kind": "video",
            "input_modalities": ["text", "image"],
            "output_modalities": ["video"],
            "per_clip": 200000,
            "recommended": True,
            "price_second": 0.10,
            "price_second_res": {"720P": 0.20, "1080P": 0.48},
            "clip_seconds": 5.0,
        },
        {
            "id": "wan2.2-t2v-plus",
            "name": "Wan 2.2 Plus (video from words)",
            "upstream": "wan2.2-t2v-plus",
            "kind": "video",
            "input_modalities": ["text"],
            "output_modalities": ["video"],
            "per_clip": 200000,
            "price_second": 0.14,
            "price_second_res": {"1080P": 0.70},
            "clip_seconds": 5.0,
        },
    ]
)

# A charge is priced at the hour it is made (DeepSeek's idle hours are half price): the
# suite prices every charge at this busy-hour moment unless a test moves the clock itself.
BUSY_HOUR = 1791439200  # 2026-10-08T06:00:00Z = 14:00 Beijing time


@pytest.fixture(autouse=True)
def _busy_hour_clock(monkeypatch):
    from nanomuse_cloud.service import Cloud

    monkeypatch.setattr(Cloud, "clock", staticmethod(lambda: BUSY_HOUR))

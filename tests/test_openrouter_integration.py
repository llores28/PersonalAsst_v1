from src.integrations import openrouter


def test_select_model_for_quality_uses_config_order() -> None:
    config = {
        "capabilities": {
            "generate_image": {
                "preferred_models": ["model-a", "model-b", "model-c"],
            },
            "analyze_image": {
                "preferred_models": ["vision-a", "vision-b"],
            },
        },
        "quality_tiers": {
            "fast": {"prefer_index": -1},
            "balanced": {"prefer_index": 0},
        },
    }

    original = openrouter._load_capability_config
    try:
        openrouter._load_capability_config = lambda: config
        assert openrouter._select_model_for_quality("balanced") == "model-a"
        assert openrouter._select_model_for_quality("fast") == "model-c"
        assert openrouter._select_model_for_quality("balanced", "analyze_image") == "vision-a"
        assert openrouter._select_model_for_quality("fast", "analyze_image") == "vision-b"
    finally:
        openrouter._load_capability_config = original


def test_infer_image_config_from_prompt_cues() -> None:
    assert openrouter._infer_image_config("Create a landscape wallpaper of mountains", "balanced") == {
        "aspect_ratio": "16:9"
    }
    assert openrouter._infer_image_config("Create a portrait phone wallpaper", "best") == {
        "aspect_ratio": "9:16",
        "image_size": "2K",
    }
    assert openrouter._infer_image_config("Create a square logo", "fast") == {
        "aspect_ratio": "1:1",
        "image_size": "1K",
    }

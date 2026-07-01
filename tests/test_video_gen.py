"""Video (image-to-video) parameter builder — the first brick of storyboard-driven
video gen. Pinned to the REAL captured i2vPro payload (2026-07-01) so we know the
builder produces exactly what PixAI's generator sends. Pure functions; no network,
no credits."""
from types import SimpleNamespace

import pixai_gallery_backup as core


def test_build_video_parameters_matches_captured_payload():
    # First-and-last-frame i2v: source + tail frame (the confirmed continuity case).
    p = core.build_video_parameters(
        "A night elf approaches the camera.", media_id="738307117258876580",
        model="v4.0.1", tail_media_id="738305581657074582", duration=15,
        mode="professional", generate_audio=True, negative="blur, low quality",
    )
    assert p["type"] == "generation-task"
    assert p["version"] == 2
    assert p["parameters"]["channel"] == "private"
    i2v = p["parameters"]["i2vPro"]
    assert i2v["model"] == "v4.0.1"
    assert i2v["mode"] == "professional"
    assert i2v["duration"] == "15"                 # seconds as a STRING
    assert i2v["generateAudio"] is True
    assert i2v["mediaId"] == "738307117258876580"
    assert i2v["tailMediaId"] == "738305581657074582"
    assert i2v["refResourceMode"] == "firstLastFrames"
    assert i2v["multiRefResource"] == {
        "imageMediaIds": [], "videoMediaIds": [], "audioMediaIds": [], "items": []}
    assert i2v["prompts"] == "A night elf approaches the camera."
    assert i2v["negativePrompts"] == "blur, low quality"
    assert i2v["usePromptsHelper"] is False


def test_single_source_image_omits_tail():
    p = core.build_video_parameters("motion", media_id="100")
    i2v = p["parameters"]["i2vPro"]
    assert i2v["mediaId"] == "100"
    assert "tailMediaId" not in i2v                 # no tail => single-source i2v
    assert i2v["model"] == core.DEFAULT_VIDEO_MODEL


def test_gen_video_parameters_from_args():
    a = SimpleNamespace(prompt="p", image="55", tail="56", duration=10,
                        model="", vmode="basic", audio=False, negative="",
                        prompt_helper=False, params_json="")
    p = core._gen_video_parameters(a)
    i2v = p["parameters"]["i2vPro"]
    assert i2v["mediaId"] == "55" and i2v["tailMediaId"] == "56"
    assert i2v["duration"] == "10" and i2v["mode"] == "basic"
    assert i2v["model"] == core.DEFAULT_VIDEO_MODEL   # empty model -> default


def test_params_json_override():
    a = SimpleNamespace(params_json='{"type":"x"}')
    assert core._gen_video_parameters(a) == {"type": "x"}


def _video_args(tmp_path, **kw):
    base = dict(out=str(tmp_path), image="55", tail="", prompt="p", negative="",
                model="", video_model="", duration=5, vmode="professional",
                audio=False, audio_language="english", video_prompt_helper=False,
                params_json="", task_id="", confirm=False)
    base.update(kw)
    return SimpleNamespace(**base)


def test_generate_video_previews_without_confirm(tmp_path):
    # No --confirm => preview only, no network, spends nothing.
    res = core.run_generate_video(_video_args(tmp_path))
    assert res == {"submitted": False}


def test_generate_video_requires_a_source_image(tmp_path):
    import pytest
    with pytest.raises(core.PixAIError):
        core.run_generate_video(_video_args(tmp_path, image=""))

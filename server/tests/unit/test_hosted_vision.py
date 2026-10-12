"""Hosted vision-language models (ADR 0010): what is sent, and what is kept of the answer.

Every provider here is driven against an in-process fake of its service's request shape —
Anthropic's through its own SDK on an `httpx2` mock transport, the OpenAI-compatible one on
an `httpx` mock transport — so no key, network or bill is involved, and each assertion is
about the request as the service would receive it.
"""

from __future__ import annotations

import base64
import io
import json
from typing import Any

import anthropic
import httpx
import httpx2
import pytest

from curvevision.core.errors import ProviderError
from curvevision.ml.anthropic_provider import FALLBACK_BETA, AnthropicProvider
from curvevision.ml.base import InferenceFrame, InferenceRequest
from curvevision.ml.openai_compatible import OpenAICompatibleProvider
from curvevision.ml.vision_llm import MAX_EDGE, MAX_PIXELS, prepared_image, read_answer

PIL = pytest.importorskip("PIL.Image")


def png(width: int = 200, height: int = 100) -> bytes:
    buffer = io.BytesIO()
    PIL.new("RGB", (width, height), "#334155").save(buffer, format="PNG")
    return buffer.getvalue()


def frame(index: int = 0, width: int = 200, height: int = 100) -> InferenceFrame:
    return InferenceFrame(
        frame=index, image=png(width, height), content_type="image/png", width=width, height=height
    )


def request(*frames: InferenceFrame, classes: list[str] | None = None) -> InferenceRequest:
    return InferenceRequest(
        model="unused",
        frames=list(frames) or [frame()],
        confidence_threshold=0.0,
        classes=classes or ["car", "Person"],
    )


ANSWER = {"objects": [{"label": "car", "box": [100, 200, 500, 800], "confidence": 0.9}]}


class TestReadingAnAnswer:
    def test_normalised_boxes_become_the_frame_s_pixels(self) -> None:
        detections = read_answer(json.dumps(ANSWER), frame(), ["car"])

        [shape] = detections.shapes
        # x: 100/1000 and 500/1000 of 200 px; y: 200/1000 and 800/1000 of 100 px.
        assert (shape.label, shape.points, shape.confidence) == ("car", [20, 20, 100, 80], 0.9)

    def test_prose_and_code_fences_around_the_json_are_tolerated(self) -> None:
        text = f"Here you go:\n```json\n{json.dumps(ANSWER)}\n```\nAnything else?"
        assert len(read_answer(text, frame(), ["car"]).shapes) == 1

    def test_a_label_is_matched_without_case_and_kept_as_the_project_spells_it(self) -> None:
        answer = {"objects": [{"label": "PERSON", "box": [0, 0, 500, 500], "confidence": 1}]}
        [shape] = read_answer(json.dumps(answer), frame(), ["Person"]).shapes
        assert shape.label == "Person"

    def test_what_cannot_be_kept_is_dropped_and_said(self) -> None:
        answer = {
            "objects": [
                {"label": "dog", "box": [0, 0, 10, 10], "confidence": 1},  # not asked for
                {"label": "car", "box": [5, 5, 5, 900], "confidence": 1},  # no width
                {"label": "car", "box": "somewhere", "confidence": 1},
                {"label": "car", "box": [900, 950, 100, -50], "confidence": 1},  # corners swapped
            ]
        }
        detections = read_answer(json.dumps(answer), frame(), ["car"])

        assert [shape.points for shape in detections.shapes] == [[20, 0, 180, 95]]
        assert len(detections.dropped) == 3
        assert any("'dog'" in reason for reason in detections.dropped)

    def test_a_confidence_given_as_a_percentage_or_not_at_all(self) -> None:
        answer = {
            "objects": [
                {"label": "car", "box": [0, 0, 500, 500], "confidence": 85},
                {"label": "car", "box": [0, 0, 500, 500]},
            ]
        }
        detections = read_answer(json.dumps(answer), frame(), ["car"])
        assert [shape.confidence for shape in detections.shapes] == [0.85, 0.5]

    @pytest.mark.parametrize("text", ["I can't see any cars.", "[1, 2, 3]", '{"cars": []}'])
    def test_an_answer_with_no_object_list_is_an_error_not_an_empty_frame(self, text: str) -> None:
        with pytest.raises(ValueError):
            read_answer(text, frame(), ["car"])


def test_a_large_frame_is_sent_small_enough_that_no_service_resizes_it_again() -> None:
    data, media_type = prepared_image(frame(width=4000, height=3000))

    with PIL.open(io.BytesIO(data)) as sent:
        assert media_type == "image/jpeg"
        assert max(sent.size) <= MAX_EDGE
        assert sent.size[0] * sent.size[1] <= MAX_PIXELS


# ----------------------------------------------------------------------------- Claude


def claude_model(model_id: str, *, images: bool = True, structured: bool = True) -> dict:
    return {
        "type": "model",
        "id": model_id,
        "display_name": model_id.replace("-", " ").title(),
        "created_at": "2026-09-01T00:00:00Z",
        "max_input_tokens": 1_000_000,
        "max_tokens": 128_000,
        "capabilities": {
            "image_input": {"supported": images},
            "structured_outputs": {"supported": structured},
        },
    }


class Claude:
    """Anthropic's API as the SDK calls it: the Models API and Messages."""

    def __init__(self, *, key: str = "sk-ant-test", answers: list[dict] | None = None) -> None:
        self.key = key
        self.models = [
            claude_model("claude-opus-5-5"),
            claude_model("claude-haiku-5-5", structured=False),
            claude_model("claude-text-only", images=False),
        ]
        self.answers = answers or [{"text": json.dumps(ANSWER)}]
        self.seen: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.seen.append(request)
        if request.headers.get("x-api-key") != self.key:
            return httpx2.Response(
                401,
                json={"type": "error", "error": {"type": "authentication_error", "message": "x"}},
            )
        path = request.url.path
        if path == "/v1/models":
            page = {"data": self.models, "has_more": False, "first_id": None, "last_id": None}
            return httpx2.Response(200, json=page)
        if path.startswith("/v1/models/"):
            model = next((m for m in self.models if m["id"] == path.rsplit("/", 1)[1]), None)
            if model is None:
                missing = {"type": "not_found_error", "message": "x"}
                return httpx2.Response(404, json={"type": "error", "error": missing})
            return httpx2.Response(200, json=model)
        if path == "/v1/messages":
            answer = self.answers[min(len(self.seen_messages) - 1, len(self.answers) - 1)]
            return httpx2.Response(
                200,
                json={
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "model": json.loads(request.content)["model"],
                    "content": [{"type": "text", "text": answer.get("text", "")}],
                    "stop_reason": answer.get("stop_reason", "end_turn"),
                    "stop_sequence": None,
                    "stop_details": answer.get("stop_details"),
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            )
        return httpx2.Response(404)

    @property
    def seen_messages(self) -> list[httpx2.Request]:
        return [r for r in self.seen if r.url.path == "/v1/messages"]


def claude(fake: Claude) -> AnthropicProvider:
    return AnthropicProvider(
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(fake))
    )


class TestClaude:
    async def test_the_model_list_is_the_models_that_take_images(self) -> None:
        listed = await claude(Claude()).list_models({"api_key": "sk-ant-test"})

        assert [model.id for model in listed] == ["claude-opus-5-5", "claude-haiku-5-5"]
        assert all(model.open_vocabulary for model in listed)

    async def test_a_wrong_key_says_so(self) -> None:
        with pytest.raises(ProviderError, match="API key"):
            await claude(Claude()).list_models({"api_key": "wrong"})

    async def test_no_key_is_refused_before_anything_is_sent(self) -> None:
        fake = Claude()
        with pytest.raises(ProviderError, match="API key"):
            await claude(fake).list_models({})
        assert fake.seen == []

    async def test_a_frame_is_sent_as_an_image_with_the_schema_and_the_fallback(self) -> None:
        fake = Claude()
        config = {"api_key": "sk-ant-test", "model": "claude-opus-5-5"}

        result = await claude(fake).infer(request(), config)

        [sent] = fake.seen_messages
        body = json.loads(sent.content)
        image, text = body["messages"][0]["content"]
        assert image["type"] == "image" and image["source"]["media_type"] == "image/jpeg"
        assert base64.b64decode(image["source"]["data"])[:2] == b"\xff\xd8"  # a JPEG
        assert '"car"' in text["text"] and '"Person"' in text["text"]
        assert body["output_config"]["format"]["type"] == "json_schema"
        assert body["fallbacks"] == "default"
        assert FALLBACK_BETA in sent.headers.get("anthropic-beta", "")
        assert [(s.label, s.points) for s in result.shapes] == [("car", [20, 20, 100, 80])]

    async def test_a_model_without_fallback_or_schema_is_asked_without_them(self) -> None:
        fake = Claude()
        config = {"api_key": "sk-ant-test", "model": "claude-haiku-5-5"}

        result = await claude(fake).infer(request(), config)

        body = json.loads(fake.seen_messages[0].content)
        assert "fallbacks" not in body and "output_config" not in body
        assert "anthropic-beta" not in fake.seen_messages[0].headers
        assert len(result.shapes) == 1

    async def test_a_declined_frame_is_reported_and_the_others_kept(self) -> None:
        fake = Claude(
            answers=[
                {"text": json.dumps(ANSWER)},
                {
                    "text": "",
                    "stop_reason": "refusal",
                    "stop_details": {"type": "refusal", "category": "cyber", "explanation": "x"},
                },
            ]
        )
        config = {"api_key": "sk-ant-test", "model": "claude-haiku-5-5"}

        result = await claude(fake).infer(request(frame(0), frame(1)), config)

        assert len(result.shapes) == 1
        assert any("declined" in warning for warning in result.warnings)

    async def test_an_unknown_model_says_so(self) -> None:
        config = {"api_key": "sk-ant-test", "model": "claude-nope"}
        with pytest.raises(ProviderError, match="no model 'claude-nope'"):
            await claude(Claude()).infer(request(), config)


# ------------------------------------------------------------------ OpenAI-compatible


class Compatible:
    """A Chat Completions service: `GET /models` and `POST /chat/completions`."""

    def __init__(self, *, content: Any = None, models: list[dict] | None = None) -> None:
        self.content = json.dumps(ANSWER) if content is None else content
        self.models = models or [
            {"id": "vision-large"},
            {"id": "text-embedding-3-small"},
            {"id": "whisper-1"},
        ]
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if request.headers.get("authorization") != "Bearer sk-test":
            return httpx.Response(401, json={"error": {"message": "bad key"}})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"object": "list", "data": self.models})
        if request.url.path == "/v1/chat/completions":
            message = {"role": "assistant", "content": self.content}
            return httpx.Response(200, json={"choices": [{"index": 0, "message": message}]})
        return httpx.Response(404)


def compatible(fake: Compatible) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(fake)))


CONFIG = {"base_url": "https://api.example.com/v1/", "api_key": "sk-test", "model": "vision-large"}


class TestOpenAICompatible:
    async def test_the_model_list_leaves_out_what_is_plainly_not_a_chat_model(self) -> None:
        listed = await compatible(Compatible()).list_models(CONFIG)
        assert [model.id for model in listed] == ["vision-large"]

    async def test_where_the_service_says_which_take_images_that_decides(self) -> None:
        models = [
            {"id": "a/vision", "architecture": {"input_modalities": ["text", "image"]}},
            {"id": "b/text", "architecture": {"input_modalities": ["text"]}},
        ]
        listed = await compatible(Compatible(models=models)).list_models(CONFIG)
        assert [model.id for model in listed] == ["a/vision"]

    async def test_a_wrong_key_and_a_wrong_base_url_each_say_so(self) -> None:
        with pytest.raises(ProviderError, match="API key"):
            await compatible(Compatible()).list_models({**CONFIG, "api_key": "nope"})
        with pytest.raises(ProviderError, match="base URL"):
            await compatible(Compatible()).list_models(
                {**CONFIG, "base_url": "https://api.example.com/v2"}
            )

    async def test_a_frame_is_sent_as_a_data_url_with_the_instruction(self) -> None:
        fake = Compatible()

        result = await compatible(fake).infer(request(), CONFIG)

        [sent] = [r for r in fake.seen if r.url.path == "/v1/chat/completions"]
        body = json.loads(sent.content)
        assert body["model"] == "vision-large"
        system, user = body["messages"]
        assert system["role"] == "system"
        text, image = user["content"]
        assert '"car"' in text["text"]
        assert image["image_url"]["url"].startswith("data:image/jpeg;base64,")
        # Nothing some implementations reject.
        assert not {"response_format", "temperature", "max_tokens"} & body.keys()
        assert [(s.label, s.points) for s in result.shapes] == [("car", [20, 20, 100, 80])]

    async def test_an_answer_in_parts_is_read_as_one(self) -> None:
        parts = [{"type": "text", "text": json.dumps(ANSWER)}]
        result = await compatible(Compatible(content=parts)).infer(request(), CONFIG)
        assert len(result.shapes) == 1

    async def test_an_unreadable_frame_is_reported_and_the_run_goes_on(self) -> None:
        result = await compatible(Compatible(content="No cars here!")).infer(request(), CONFIG)
        assert result.shapes == []
        assert any("Frame 0" in warning for warning in result.warnings)

# ADR 0010 — Hosted vision-language models as built-in providers

**Status:** Accepted · **Date:** 2026-10 · **Amends:** [ADR 0005](./0005-http-inference-contract.md)

## Context

ADR 0005 chose an HTTP inference contract over a bundled serving platform, and accepted
the cost plainly: "A new user cannot click *auto-annotate* without standing something up
first." Iteration 61 made registering such a server a URL and a key, and that exposed how
large the remaining cost is — the product owner asked for AI annotation that works by adding
an API key, for the services people already hold keys for: OpenAI, Anthropic's Claude,
OpenRouter, xAI, and the rest.

None of those speak CurveVision's contract, and none needs to. They are vision-language
models behind one of two request shapes: Anthropic's Messages API, and the OpenAI Chat
Completions shape that OpenAI, xAI, OpenRouter, Google Gemini (its compatibility endpoint),
Mistral, Groq, Together and a local Ollama all accept.

## Options

1. **Keep ADR 0005 as it is** and publish adapter servers that translate the contract to
   each vendor. Every user would still run a server first — the cost this is meant to remove.
2. **One provider per vendor**, each with its own SDK. Eight dependencies, eight surfaces to
   keep current, for request shapes that are mostly identical.
3. **Two built-in providers, by request shape:** `anthropic`, on Anthropic's official Python
   SDK, and `openai_compatible`, over plain HTTP to any endpoint that accepts the Chat
   Completions shape. A preset list in the application maps a service name to its base URL.

## Decision

**Option 3.** The HTTP contract stays exactly as it was, for self-hosted and specialised
models; the two new providers sit beside it behind the same `ModelProvider` protocol, so the
editor, the suggestions workflow and the run endpoint do not change.

A hosted model is registered as **open vocabulary**: it is asked for the project's own label
names, by name, and answers with boxes in coordinates normalised to 0–1000. Normalised
coordinates are the one convention that survives the resizing every service applies to an
image before the model sees it, by rules the caller cannot observe.

## Consequences

**We accept:**

* **Boxes from a general vision-language model are approximate.** These models are good at
  *what* is in a frame and loose about *exactly where*; most return no calibrated
  confidence. That is tolerable here only because predictions land as suggestions an
  annotator accepts, corrects or rejects — never as finished annotations. Masks, polygons
  and keypoints are not asked of them.
* **Frames leave the machine.** Each frame a hosted model is run on is sent to that service
  and billed to the key. In the desktop shape this is a change of kind — local data going to
  a third party — so the application says so where the model is added. Ollama is the
  in-between: the same request shape, served on the user's own machine.
* **A vendor SDK is a dependency again**, for Anthropic only: `anthropic` (MIT) and what it
  brings — `httpx2`, `jiter`, `docstring-parser`. ADR 0005's "never imports a vendor SDK"
  is amended to "imports one, where using it is the supported way to call that service".
* **Keys are stored as given**, like every other registration's config and like a webhook's
  signing secret: never returned by the API, readable by anyone who can read the database.
* **The preset list goes stale.** Base URLs move. The presets are a convenience in the web
  client; *Other (OpenAI-compatible)* takes any base URL, and model names always come from
  the service's own model list rather than from a table in this repository.

**We keep from ADR 0005:** no model weights in the repository or the image, no serving
platform, and a test suite that exercises every provider against in-process fakes of each
request shape, with no network and no key.

**What this is not:** a claim that a general vision-language model replaces a trained
detector. For production labelling volume a fine-tuned detector behind the HTTP contract is
faster, cheaper per frame, and more precise; a hosted model is how a project gets its first
suggestions without one.

from __future__ import annotations

import json
import logging
import base64

from agents import function_tool

from src.integrations.openrouter import analyze_image, generate_image, list_models_by_modality, serialize_image_result
from src.skills.definition import SkillDefinition, SkillGroup

logger = logging.getLogger(__name__)


def _build_bound_openrouter_tools(bound_user_id: int) -> list:
    @function_tool(name_override="generate_image")
    async def generate_image_tool(prompt: str, quality: str = "balanced") -> str:
        """Generate an image from a text prompt using OpenRouter."""
        result = await generate_image(user_id=bound_user_id, prompt=prompt, quality=quality)
        return serialize_image_result(result)

    @function_tool(name_override="analyze_uploaded_image")
    async def analyze_uploaded_image_tool(prompt: str = "Describe this image in detail.", quality: str = "balanced") -> str:
        """Analyze the user's most recently uploaded Telegram photo using OpenRouter."""
        from src.memory.conversation import get_session_field

        raw_payload = await get_session_field(bound_user_id, "latest_uploaded_image")
        if not raw_payload:
            return "I don't have a recent uploaded image to analyze yet. Ask the user to send a photo first."

        payload = json.loads(raw_payload)
        image_base64 = payload.get("data_base64")
        mime_type = payload.get("mime_type") or "image/jpeg"
        if not image_base64:
            return "I couldn't read the uploaded image data. Ask the user to send the photo again."

        result = await analyze_image(
            user_id=bound_user_id,
            prompt=prompt,
            image_bytes=base64.b64decode(image_base64),
            mime_type=mime_type,
            quality=quality,
        )
        return result.analysis

    @function_tool(name_override="list_openrouter_models")
    async def list_openrouter_models_tool(modality: str = "image") -> str:
        """List available OpenRouter models for a given output modality, sorted cheapest first.

        Args:
            modality: What the model produces — "image", "video", or "audio".
                      Use "video" for talking-head, lip-sync, image-to-video, text-to-video.
                      Use "audio" for music generation, text-to-speech, sound effects.
                      Use "image" for image generation from a prompt.

        Returns a summary of the top 8 cheapest models with id, description, and pricing.
        """
        models = await list_models_by_modality(modality)
        if not models:
            return f"No OpenRouter {modality} models are currently available."

        lines = [f"OpenRouter {modality} models (cheapest first):"]
        for m in models[:8]:
            model_id = m.get("id") or "unknown"
            name = m.get("name") or model_id
            desc = (m.get("description") or "")[:120]
            pricing = m.get("pricing") or {}
            price_str = " | ".join(
                f"{k}: {v}" for k, v in pricing.items() if v and v != "0"
            )
            lines.append(f"- **{model_id}** ({name})")
            if desc:
                lines.append(f"  {desc}")
            if price_str:
                lines.append(f"  Pricing: {price_str}")
        return "\n".join(lines)

    return [generate_image_tool, analyze_uploaded_image_tool, list_openrouter_models_tool]


def build_openrouter_skill(user_id: int) -> SkillDefinition:
    return SkillDefinition(
        id="openrouter_images",
        group=SkillGroup.INTERNAL,
        description=(
            "Generate images from prompts, analyze uploaded photos, and discover & use "
            "OpenRouter AI models for video generation, talking-avatar/lip-sync, and music/audio creation."
        ),
        tools=_build_bound_openrouter_tools(user_id),
        instructions=(
            "You have access to OpenRouter — a gateway to hundreds of AI models for image, video, and audio generation. "
            "Use these tools cohesively based on what the user wants to create.\n\n"

            "## Image Generation\n"
            "Use `generate_image` when the user asks to create, draw, render, or generate any image, picture, photo, illustration, logo, or artwork from a text description. "
            "After generating the image, briefly describe what you created.\n\n"

            "## Photo Analysis\n"
            "Use `analyze_uploaded_image` when the user sends a photo in Telegram and asks what is in it, wants a caption, description, or analysis. "
            "Do NOT use this for Google Drive files or document attachments.\n\n"

            "## Video Generation, Talking Avatar & Lip-Sync\n"
            "When the user asks to create a video — whether text-to-video, image-to-video, a talking avatar, "
            "lip-sync animation, or making a photo 'speak' — follow this workflow:\n"
            "1. Call `list_openrouter_models` with modality='video' to discover the current cheapest models.\n"
            "2. Choose the lowest-cost model that supports the required capability:\n"
            "   - Talking head / lip-sync / image speaks: prefer alibaba/wan-2.6 (native lip-sync, 9:16 support) "
            "     or bytedance/seedance-1-5-pro (multi-language lip-sync, unified audio+video).\n"
            "   - Simple image-to-video / text-to-video: prefer the cheapest model from the list.\n"
            "3. Explain to the user which model you selected, why (cheapest that fits the task), and that video generation "
            "   takes 1–5 minutes via an async polling API.\n"
            "4. For talking-head requests: confirm the user has sent a photo (it's stored as latest_uploaded_image in session). "
            "   If not, ask them to send one first. Extract the exact speech text from their request.\n"
            "5. Describe exactly how the call would be made via the OpenRouter /api/v1/videos endpoint:\n"
            "   POST https://openrouter.ai/api/v1/videos with: model, prompt, aspect_ratio (9:16 for vertical), "
            "   resolution (720p default), duration (6s default), and frame_images[first_frame] containing the base64 image.\n"
            "6. The API returns a job_id + polling_url immediately (202 Accepted). Poll every 15s until status=completed, "
            "   then download the mp4 from unsigned_urls[0].\n"
            "NOTE: If OpenRouter video generation is not yet wired end-to-end in code, be transparent: explain the model chosen, "
            "the parameters, the expected cost from the pricing list, and ask the user if they want you to proceed or "
            "if they want to set up the OPENROUTER_API_KEY and enable video first.\n\n"

            "## Music & Audio Generation\n"
            "When the user asks to create music, a song, sound effects, background audio, or any audio content:\n"
            "1. Call `list_openrouter_models` with modality='audio' to discover current models and pricing.\n"
            "2. Select the cheapest model that fits the request (music generation vs TTS vs sound effects).\n"
            "3. Explain the selected model, its capabilities, estimated cost, and the expected output format.\n"
            "4. Audio generation typically uses POST /api/v1/audio/generations or the chat completions endpoint "
            "   depending on the model — check the model description for the correct endpoint.\n"
            "5. Be transparent about capability: if audio generation requires additional setup, explain clearly what's needed.\n\n"

            "## Model Discovery\n"
            "Use `list_openrouter_models` with the appropriate modality whenever:\n"
            "- The user asks which models are available for video, audio, or image.\n"
            "- You need to select the cheapest or best model for a generation task.\n"
            "- The user asks about pricing for AI generation tasks.\n\n"

            "## General Principles\n"
            "- Always prefer the cheapest model that meets the task requirements.\n"
            "- Be transparent: tell the user which model you picked, why, and the estimated cost.\n"
            "- For any async generation (video, audio): set clear expectations about wait time upfront.\n"
            "- If a capability is not yet implemented end-to-end, say so honestly and describe what the user needs to enable it."
        ),
        routing_hints=[
            "Image generation: 'create an image', 'generate a picture', 'draw something', 'make artwork', 'render a logo', 'design a banner'",
            "Image analysis: 'what is in this photo', 'describe this image', 'analyze the uploaded picture'",
            "Video generation: 'create a video', 'make a video', 'generate a clip', 'text to video', 'image to video'",
            "Talking avatar: 'make this image talk', 'have the photo say', 'lip sync', 'talking head', 'animate my photo', 'make her speak'",
            "Music/audio: 'create music', 'generate a song', 'make background music', 'create sound effects', 'compose audio'",
            "Model discovery: 'what video models are available', 'cheapest image model', 'list openrouter models'",
            "NOT for Google Workspace files, Drive documents, or calendar/email tasks",
        ],
        read_only=False,
        tags=[
            "image", "picture", "photo", "art", "draw", "render",
            "video", "talking", "lipsync", "avatar", "animate",
            "music", "audio", "song", "sound",
            "openrouter", "generate", "create",
        ],
    )

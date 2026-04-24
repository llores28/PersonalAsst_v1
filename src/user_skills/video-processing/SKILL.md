---
name: "Video Processing"
description: "Handles the compositing and processing of video files."
version: 1.0.0
author: atlas-setup
group: "user"
tags: 
  - "video"
  - "processing"
  - "ffmpeg"
  - "composition"
routing_hints: 
  - "process video"
  - "apply transitions"
  - "adjust aspect ratio"
  - "integrate audio"
requires_skills: []
extends_skill: null
tools: []
requires_connection: false
read_only: true
---

## Purpose
This skill enables the processing of video files, including compositing, transitions, and audio integration.

## When to Use
Use this skill when a task involves processing existing video files, such as compositing clips or applying transitions.

## Process
1. Receive task details from ProjectManager.
2. Use ffmpeg_video_composition for compositing tasks.
3. Apply transitions with ffmpeg_apply_transitions.
4. Adjust aspect ratio using ffmpeg_aspect_ratio.
5. Normalize audio with ffmpeg_audio_normalization.
6. Integrate audio using ffmpeg_audio_integration.

## Tools
- ffmpeg_video_composition
- ffmpeg_apply_transitions
- ffmpeg_aspect_ratio
- ffmpeg_audio_normalization
- ffmpeg_audio_integration

## Examples
- User requests to "process video clips with transitions"; the agent uses ffmpeg_apply_transitions.
- User asks to "integrate background music"; the agent uses ffmpeg_audio_integration.

## Available Tools

When this skill is active, prefer calling these registered tools from the **FFmpeg Video Composer** organization before falling back to external search or generic reasoning:

- `ffmpeg_video_composition`
- `ffmpeg_apply_transitions`
- `ffmpeg_aspect_ratio`
- `ffmpeg_audio_normalization`
- `ffmpeg_audio_integration`

## Routing Hints

This skill is selected when the user mentions any of: `process video`, `apply transitions`, `adjust aspect ratio`, `integrate audio`. When selected, follow the instructions above and call the listed tools with the parameters declared in each tool's manifest.

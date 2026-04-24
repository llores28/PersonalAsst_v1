---
name: "FFmpeg Video Composition"
description: "Enables compositing and processing of video files using FFmpeg."
version: 1.0.0
author: atlas-setup
group: "user"
tags: 
  - "video"
  - "audio"
  - "processing"
  - "ffmpeg"
routing_hints: 
  - "compose video"
  - "apply transitions"
  - "process video files"
  - "integrate audio"
  - "video editing"
requires_skills: []
extends_skill: null
tools: []
requires_connection: false
read_only: true
---

## Purpose
This skill allows agents to process and compose video files using FFmpeg, applying transitions and integrating audio tracks as needed.

## When to Use
Use this skill when the task involves processing existing video or audio files, such as applying transitions or integrating audio tracks.

## Process
1. Receive task details specifying the processing requirements.
2. Use FFmpeg tools to apply the necessary processing steps.
3. Verify the output file is created and meets quality standards.
4. Return the processed file path to the requesting agent.

## Tools
Use FFmpeg tools such as ffmpeg_video_composition and ffmpeg_apply_transitions in the specified order.

## Examples
- User request: "Compose a video with these clips and add transitions." Expected agent response: "Video composed successfully, file path: /path/to/video.mp4."
- User request: "Integrate this audio track into the video." Expected agent response: "Audio integrated successfully, file path: /path/to/final_video.mp4."

## Routing Hints

This skill is selected when the user mentions any of: `compose video`, `apply transitions`, `process video files`, `integrate audio`, `video editing`. When selected, follow the instructions above and call the listed tools with the parameters declared in each tool's manifest.

# User Skills Directory

This directory contains user-created skills that extend the Personal Assistant's capabilities.

## What are Skills?

Skills are packages of expertise that guide the assistant's behavior. They can include:
- **Instructions**: How to perform specific tasks
- **Knowledge**: Domain expertise and reference information
- **Resources**: Templates, examples, and reference documents
- **Tools**: Custom scripts and functions (future capability)

## Creating a Skill

1. Create a new folder with a descriptive name (e.g., `my-custom-skill`)
2. Add a `SKILL.md` file with YAML frontmatter and instructions
3. (Optional) Add resources in `resources/`, scripts in `scripts/`, templates in `templates/`

### SKILL.md Format

```yaml
---
name: My Custom Skill
description: What this skill does
version: 1.0.0
author: your-name
tags: [tag1, tag2]
routing_hints:
  - "when you need to do X"
  - "for handling Y situations"
requires_skills: []  # Dependencies on other skill IDs
extends_skill: null  # Skill ID this extends (optional)
tools: []  # For future use
requires_connection: false
read_only: true
---

# Skill Instructions

Write your detailed instructions here using Markdown formatting.
These will be injected into the AI's context when the skill is triggered.

## Examples

Include examples of how to use this skill.
```

## Example Skills

### Knowledge-Only Skills
Skills without tools that provide expertise:
- `devotional-style-guide/` - Guidelines for generating devotionals

### Future: Tool-Enabled Skills
Skills that include custom scripts (coming soon):
- Data processing skills
- API integration skills
- File manipulation skills

## Directory Structure

```
user_skills/
├── README.md
├── devotional-style-guide/
│   └── SKILL.md
├── my-custom-skill/
│   ├── SKILL.md           # Required: Main skill definition
│   ├── resources/         # Optional: Additional docs
│   │   └── reference.md
│   ├── scripts/           # Optional: Executable scripts (future)
│   │   └── helper.py
│   └── templates/         # Optional: Jinja2 templates (future)
│       └── template.j2
```

## Loading Skills

Skills in this directory are automatically loaded when the assistant starts. Changes require a restart (hot-reload coming in a future update).

## Marketplace

In the future, you'll be able to:
- Browse the skill marketplace from the Dashboard
- Install skills shared by other users
- Publish your own skills for others to use
- Rate and review skills

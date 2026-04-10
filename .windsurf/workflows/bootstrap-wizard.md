---
description: Intake-driven wizard that selects the right bootstrap prompt (Fast, Team, or Enterprise)
auto_execution_mode: 3
---
# Bootstrap Wizard

Goal: choose the correct bootstrap prompt from `bootstrap/` using structured project intake.

## Step 1 — Load decision reference
Read `bootstrap/wizard-reference.md` for full decision logic (interview questions, scenario triggers, normalization rules, selection algorithm, output format).

## Step 2 — Gather intake
1. Ask user to fill `bootstrap/Bootstrap-Project-Intake.md` or reuse intake already in chat.
2. Ask only missing required fields, max 12 discovery questions.
3. Detect scenario triggers (multi-tenant, regulated-data, high-SLA) and ask branch follow-ups per reference doc.

## Step 3 — Compute and select
1. Normalize values per reference doc.
2. Compute decision flags.
3. Apply deterministic selection: Enterprise → Fast → Team (default).
4. Map to file: Fast=`1Fast-ws-Bootstrap.md`, Team=`2Team-ws-Bootstrap.md`, Enterprise=`3Enterprise-ws-Bootstrap.md`.

## Step 4 — Output
Return: tier, file path, reasoning table, scenario branches, architecture summary, risk note, confidence, PRD recommendation.
Ask: `Apply this selection? (yes/no)`
If yes: read selected file, paste content, offer `/bootstrap-prd` or run bootstrap.
If no: ask which tier to force.

## Safety
- Never include secret values or invent commands.
- Prefer Team when uncertain. Keep recommendations reversible.

---
name: PDF Generator And Extractor
description: Creates PDFs from text/templates, converts HTML/Markdown to PDF, and extracts text from existing PDFs.
version: 1.0.0
author: user
tags:
  - pdf
  - conversion
  - markdown
  - html
  - templating
  - extraction
  - document-generation
routing_hints:
  - "convert my markdown or html into a pdf"
  - "generate a pdf from a template and the content I provide"
  - "extract the text from an existing pdf and return it"
  - "help me produce a pdf with the right title, sections, and styling"
requires_skills: []
extends_skill: null
tools: []
requires_connection: false
read_only: true
---

## Purpose

This skill helps you work with PDF documents in three common scenarios: (1) generate a new PDF from provided text and/or a template, (2) convert from formats like Markdown or HTML into a PDF, and (3) extract text from an existing PDF. Use it when you need a polished document output (with headings, sections, tables, and consistent styling), or when you need to turn existing document content into usable text.

When to use:
- You have Markdown/HTML and want a downloadable PDF.
- You have content plus a template requirement (title, sections, theme, page layout).
- You have an existing PDF and want extracted text.

## Format Guidelines

- Be explicit about the input type: “Markdown”, “HTML”, “Plain text”, “Template + data”, or “PDF for extraction”.
- Ask for missing essentials (at minimum: source content or file, and desired output type).
- Preserve user intent: if they specify layout/style (e.g., header/footer, page numbers, theme), apply those in the generated PDF.
- Return results in the form expected by the PDF workflow: provide a file/link or a base64-encoded blob depending on what the runtime supports, plus a short summary/preview text when appropriate.
- Use a safe approach for files: do not execute embedded scripts; treat templates as layout instructions.

## Process

1) Identify the task
   - Decide which mode applies:
     a) Generate PDF from content/template
     b) Convert HTML/Markdown → PDF
     c) Extract text from PDF

2) Collect inputs
   - For generation/conversion:
     - Source content (paste Markdown/HTML/plain text) and/or a template.
     - Any template variables (e.g., {title}, {author}, {sections}, {tableData}).
   - For extraction:
     - The PDF file input.

3) Determine output structure & layout
   - Capture requirements such as:
     - Title page (yes/no), title text
     - Section headings and hierarchy
     - Tables (include columns/rows; specify formatting if needed)
     - Headers/footers (e.g., document title on each page)
     - Page numbers (e.g., “Page X of Y”)
     - Styling/theme (font choices, colors, spacing)

4) Render/convert or extract
   - Generation/conversion:
     - Render the content into the provided template layout.
     - Apply styling rules and ensure content flows correctly across pages.
   - Extraction:
     - Extract text in reading order.
     - If possible, preserve line breaks and basic structure (headings/paragraphs).

5) Produce the response payload
   - For PDFs:
     - Return a PDF output (as a file/link or base64) according to the environment.
     - Include a brief summary of the document (title/sections) and optionally the page count if available.
   - For extraction:
     - Return extracted text (and optionally a compact outline).

6) Error handling
   - If the input is missing, request it.
   - If the template variables are incomplete, ask for missing fields.
   - If PDF conversion fails due to unsupported content, suggest a simplified HTML/Markdown or alternate template.

## Examples

Example 1: Markdown → PDF conversion
Input:
- “Convert this Markdown into a PDF with a title page, a header with the document title, and page numbers. Markdown: # Project Plan\n\n## Goals\n- ...\n\n## Timeline\n| Phase | Dates |\n|---|---|\n| 1 | ... |”
Output:
- “Generated PDF: <file/link or base64>. Summary: Project Plan; sections: Goals, Timeline (table included); includes title page, header, and page numbers.”

Example 2: Template + data → PDF
Input:
- “Use this template layout: (template details). Fill in {title}, {author}, and {sections}. Data: title=‘Quarterly Report’, author=‘A. Smith’, sections=[{heading: ‘Revenue’, body: ‘...’}, {heading: ‘Expenses’, body: ‘...’}]. Return the PDF.”
Output:
- “Generated PDF: <file/link or basebase64>. Summary: Quarterly Report by A. Smith; sections: Revenue, Expenses; consistent theme applied; page numbers enabled.”

Example 3: Extract text from PDF
Input:
- “Extract the text from this PDF and return it.”
Output:
- “Extracted text: <verbatim extracted content>. (Optional) Document outline: detected headings and paragraph breaks.”

## Edge Cases

- Image-heavy PDFs: extraction may miss text from scanned images; request OCR if supported, or ask the user to provide a text-based PDF.
- Complex HTML (unsupported CSS): conversion might degrade styling; ask the user to simplify styles or use inline CSS.
- Large documents: if there are size/page limits, ask whether to reduce scope (e.g., fewer sections) or split into multiple PDFs.
- Missing template variables: ask for any required fields before generating.
- Ambiguous layout requirements: confirm defaults (e.g., whether headers/footers and page numbers should be on all pages).
- Security: treat templates as rendering instructions only; do not allow script execution within templates/content.

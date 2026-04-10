# Telegram Long Message Fix

## Problem
Atlas generates comprehensive Drive organization plans (7102+ characters) but Telegram has a 4096 character limit per message. This caused "Something went wrong" errors when trying to send the full organization plan.

## Root Cause
- Telegram API limit: 4096 UTF-8 characters per message
- Atlas generates detailed plans with file lists, often exceeding 7000 characters
- No message splitting mechanism in the bot handler

## Solution Implemented

### 1. Message Splitting (handler_utils.py)
- Added automatic message splitting for responses > 4096 characters
- Preserves markdown formatting by splitting at line boundaries
- Adds continuation indicators: `(1/3)`, `...(2/3)`, etc.
- Falls back to plain text if markdown parsing fails

### 2. Splitting Logic
```python
MAX_LENGTH = 4096
- Split by lines to avoid breaking markdown entities
- Track current part length
- Start new part when adding line would exceed limit
- Send each part with continuation indicators
```

### 3. User Experience
**Before:**
- "Something went wrong. I've logged the error. Please try again."

**After:**
- `(1/3)` - First part of organization plan
- `...(2/3)` - Continuation with more files
- `...(3/3)` - Final part with next steps

## Testing Results
✅ 5000+ char messages split into 3 parts
✅ Continuation indicators working
✅ Markdown preserved
✅ Each part under 4096 limit

## Files Modified
- `src/bot/handler_utils.py`: Enhanced `_answer_with_markdown_fallback` with message splitting

## Impact
- Users can now receive complete Drive organization plans
- No more "Something went wrong" for long responses
- Better user experience with clear message continuation

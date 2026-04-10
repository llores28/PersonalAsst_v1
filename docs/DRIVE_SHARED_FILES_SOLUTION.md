# Drive Shared Files Solution

## Problem
Atlas attempts to rename shared files/folders that the user doesn't own, resulting in `insufficientFilePermissions` errors. The error message was confusing (suggested re-authentication) when the real issue is file-level permissions.

## Root Cause
- Files "Documents" (ID: 1kj1u1bW5-ZD8Ia7FQRXTgDugIvhC-scw) and "Untitled document" (ID: 1m3h-9OKYBpDRQbNyrmHyXi_pqRRxv7cYU5kpK-6hVP8) are **shared with** lannys.lores@gmail.com, not **owned by** them
- Shared files often have view-only permissions
- The Drive API returns 403 insufficientFilePermissions for non-owners

## Solution Implemented

### 1. Improved Error Messages (workspace_mcp.py)
- Intercept `insufficientFilePermissions` in tool results
- Return `[PERMISSION ERROR]` with clear explanation:
  - "You don't have edit access to this file"
  - "The file is likely shared with you as view-only"
  - "Do NOT suggest re-authenticating — the issue is file-level permissions, not OAuth"

### 2. Pre-flight Ownership Check (drive_agent.py)
- Added `check_drive_file_ownership` tool
- Updated `rename_connected_drive_file` to check ownership before attempting rename
- If file is shared, suggests creating a copy instead

### 3. Ownership Detection Logic
```python
if "Shared: True" in result:
    return "File is SHARED (view-only or limited access). Cannot rename."
elif "Shared: False" in result:
    return "File is OWNED by user. Can rename."
```

## Testing Results
✅ Documents folder: Detected as SHARED - cannot rename
✅ Untitled document: Detected as SHARED - cannot rename
✅ 06_Misc_To_Classify folder: Owned by user - can rename (verified earlier)

## User Experience Improvement
**Before:**
- "Both renames were blocked... you might need to re-authenticate"

**After:**
- "Cannot rename: File is SHARED (view-only). Suggestion: Create a copy with the desired name instead."

## Alternative Solutions Considered
1. **Request Edit Access**: Guide user to contact file owner
2. **Make Copy**: Create editable copy with desired name
3. **Move to Own Folder**: Copy to user-owned folder then rename

## Future Enhancements
1. Batch ownership checking for multiple files
2. Automatic copy suggestion for shared files
3. Filter search results to show owned files first

## Files Modified
- `src/integrations/workspace_mcp.py`: Improved error message handling
- `src/agents/drive_agent.py`: Added ownership check tool

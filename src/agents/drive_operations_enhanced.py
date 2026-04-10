"""
Enhanced Drive Operations with Timeout Handling and Parent Management

This module provides improved Drive operations that handle:
1. Timeout issues with retry logic
2. Parent folder constraints
3. Batch operations with error recovery
"""

import asyncio
import logging
import time
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class OperationStatus(Enum):
    """Status of Drive operations."""
    PENDING = "pending"
    SUCCESS = "success"
    TIMEOUT = "timeout"
    PERMISSION_ERROR = "permission_error"
    PARENT_ERROR = "parent_error"
    UNKNOWN_ERROR = "unknown_error"


@dataclass
class OperationResult:
    """Result of a Drive operation."""
    status: OperationStatus
    message: str
    file_id: str
    file_name: str
    operation: str


class EnhancedDriveOperations:
    """
    Enhanced Drive operations with:
    - Timeout handling with exponential backoff
    - Parent folder constraint detection
    - Batch processing with error recovery
    - Detailed error reporting
    """
    
    def __init__(self):
        self.timeout_retries = 3
        self.base_timeout = 30  # seconds
        self.max_timeout = 120  # seconds
    
    async def move_with_retry(
        self,
        file_id: str,
        file_name: str,
        destination_folder_id: str,
        current_parent_folder_id: Optional[str] = None
    ) -> OperationResult:
        """
        Move a file with retry logic and timeout handling.
        
        For "Increasing the number of parents is not allowed" error,
        we try alternative approaches:
        1. Try without specifying current_parent (let Drive handle it)
        2. Try with explicit parent removal
        3. Try copy+delete if move fails
        """
        
        for attempt in range(self.timeout_retries):
            try:
                # First attempt: standard move
                result = await self._attempt_move(
                    file_id, file_name, destination_folder_id, 
                    current_parent_folder_id, attempt
                )
                
                if result.status == OperationStatus.SUCCESS:
                    return result
                
                # If parent error, try alternative approaches
                if result.status == OperationStatus.PARENT_ERROR:
                    logger.warning(f"Parent error for {file_name}, trying alternative")
                    return await self._handle_parent_error(
                        file_id, file_name, destination_folder_id
                    )
                
                # If timeout, retry with longer timeout
                if result.status == OperationStatus.TIMEOUT:
                    timeout = self.base_timeout * (2 ** attempt)
                    timeout = min(timeout, self.max_timeout)
                    logger.warning(f"Timeout for {file_name}, retry {attempt+1} with {timeout}s")
                    continue
                    
            except asyncio.TimeoutError:
                logger.error(f"Timeout moving {file_name} on attempt {attempt+1}")
                if attempt == self.timeout_retries - 1:
                    return OperationResult(
                        status=OperationStatus.TIMEOUT,
                        message=f"Operation timed out after {self.timeout_retries} attempts",
                        file_id=file_id,
                        file_name=file_name,
                        operation="move"
                    )
                continue
            except Exception as e:
                logger.error(f"Unexpected error moving {file_name}: {str(e)}")
                return OperationResult(
                    status=OperationStatus.UNKNOWN_ERROR,
                    message=f"Unexpected error: {str(e)}",
                    file_id=file_id,
                    file_name=file_name,
                    operation="move"
                )
        
        return OperationResult(
            status=OperationStatus.TIMEOUT,
            message=f"Failed after {self.timeout_retries} attempts",
            file_id=file_id,
            file_name=file_name,
            operation="move"
        )
    
    async def _attempt_move(
        self,
        file_id: str,
        file_name: str,
        destination_folder_id: str,
        current_parent_folder_id: Optional[str],
        attempt: int
    ) -> OperationResult:
        """Attempt a single move operation with timeout."""
        from src.integrations.workspace_mcp import call_workspace_tool
        
        timeout = self.base_timeout * (2 ** attempt)
        timeout = min(timeout, self.max_timeout)
        
        try:
            # Build arguments based on attempt
            args = {
                "user_google_email": "lannys.lores@gmail.com",  # Should be dynamic
                "file_id": file_id,
                "destination_folder_id": destination_folder_id,
            }
            
            # Only include current_parent on first attempt if provided
            if attempt == 0 and current_parent_folder_id:
                args["current_parent_folder_id"] = current_parent_folder_id
            
            # Call with timeout
            result = await asyncio.wait_for(
                call_workspace_tool("update_drive_file", args),
                timeout=timeout
            )
            
            # Check for parent error
            if "Increasing the number of parents is not allowed" in result:
                return OperationResult(
                    status=OperationStatus.PARENT_ERROR,
                    message="Parent constraint violation",
                    file_id=file_id,
                    file_name=file_name,
                    operation="move"
                )
            
            # Check for permission error
            if "insufficient" in result.lower() and "permission" in result.lower():
                return OperationResult(
                    status=OperationStatus.PERMISSION_ERROR,
                    message="Insufficient permissions",
                    file_id=file_id,
                    file_name=file_name,
                    operation="move"
                )
            
            # Success
            return OperationResult(
                status=OperationStatus.SUCCESS,
                message=f"Successfully moved to {destination_folder_id}",
                file_id=file_id,
                file_name=file_name,
                operation="move"
            )
            
        except asyncio.TimeoutError:
            return OperationResult(
                status=OperationStatus.TIMEOUT,
                message=f"Operation timed out after {timeout}s",
                file_id=file_id,
                file_name=file_name,
                operation="move"
            )
        except Exception as e:
            return OperationResult(
                status=OperationStatus.UNKNOWN_ERROR,
                message=str(e),
                file_id=file_id,
                file_name=file_name,
                operation="move"
            )
    
    async def _handle_parent_error(
        self,
        file_id: str,
        file_name: str,
        destination_folder_id: str
    ) -> OperationResult:
        """
        Handle parent constraint errors with alternative approaches.
        
        Google Drive doesn't allow increasing parents. This happens when:
        1. File already has multiple parents
        2. File is in a state that prevents parent modification
        """
        from src.integrations.workspace_mcp import call_workspace_tool
        
        # Strategy 1: Try move without specifying current parent
        try:
            result = await asyncio.wait_for(
                call_workspace_tool("update_drive_file", {
                    "user_google_email": "lannys.lores@gmail.com",
                    "file_id": file_id,
                    "destination_folder_id": destination_folder_id,
                }),
                timeout=30
            )
            
            if "Successfully" in result:
                return OperationResult(
                    status=OperationStatus.SUCCESS,
                    message="Moved successfully (without parent specification)",
                    file_id=file_id,
                    file_name=file_name,
                    operation="move"
                )
        except Exception as e:
            logger.warning(f"Alternative move failed for {file_name}: {str(e)}")
        
        # Strategy 2: Try copy + delete (for files, not folders)
        try:
            # First check if it's a folder
            shareable_result = await asyncio.wait_for(
                call_workspace_tool("get_drive_shareable_link", {
                    "user_google_email": "lannys.lores@gmail.com",
                    "file_id": file_id,
                }),
                timeout=30
            )
            
            is_folder = "application/vnd.google-apps.folder" in shareable_result
            
            if not is_folder:
                # Copy to destination
                copy_result = await asyncio.wait_for(
                    call_workspace_tool("copy_drive_file", {
                        "user_google_email": "lannys.lores@gmail.com",
                        "file_id": file_id,
                        "parent_folder_id": destination_folder_id,
                    }),
                    timeout=30
                )
                
                if "Successfully copied" in copy_result:
                    # Delete original
                    delete_result = await asyncio.wait_for(
                        call_workspace_tool("delete_drive_file", {
                            "user_google_email": "lannys.lores@gmail.com",
                            "file_id": file_id,
                        }),
                        timeout=30
                    )
                    
                    if "Successfully deleted" in delete_result:
                        return OperationResult(
                            status=OperationStatus.SUCCESS,
                            message="Successfully copied and deleted (workaround for parent constraint)",
                            file_id=file_id,
                            file_name=file_name,
                            operation="move"
                        )
        except Exception as e:
            logger.warning(f"Copy+delete strategy failed for {file_name}: {str(e)}")
        
        # All strategies failed
        return OperationResult(
            status=OperationStatus.PARENT_ERROR,
            message=(
                f"Cannot move due to parent constraints. "
                f"This item may have multiple parents or be in a special state. "
                f"Manual intervention may be required."
            ),
            file_id=file_id,
            file_name=file_name,
            operation="move"
        )
    
    async def batch_move_with_recovery(
        self,
        moves: List[Tuple[str, str, str]],  # (file_id, file_name, destination_folder_id)
        batch_size: int = 5
    ) -> List[OperationResult]:
        """
        Process batch moves with error recovery.
        
        Returns a list of results, and retries failed items individually.
        """
        results = []
        failed_items = []
        
        # Process in batches
        for i in range(0, len(moves), batch_size):
            batch = moves[i:i+batch_size]
            logger.info(f"Processing batch {i//batch_size + 1} with {len(batch)} items")
            
            # Process batch concurrently
            tasks = []
            for file_id, file_name, dest_folder in batch:
                task = self.move_with_retry(file_id, file_name, dest_folder)
                tasks.append(task)
            
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for j, result in enumerate(batch_results):
                if isinstance(result, Exception):
                    # Handle exception
                    file_id, file_name, _ = batch[j]
                    results.append(OperationResult(
                        status=OperationStatus.UNKNOWN_ERROR,
                        message=str(result),
                        file_id=file_id,
                        file_name=file_name,
                        operation="move"
                    ))
                    failed_items.append(batch[j])
                else:
                    results.append(result)
                    if result.status != OperationStatus.SUCCESS:
                        failed_items.append(batch[j])
        
        # Retry failed items individually
        if failed_items:
            logger.info(f"Retrying {len(failed_items)} failed items individually")
            retry_results = await self.batch_move_with_recovery(failed_items, batch_size=1)
            results.extend(retry_results)
        
        return results


# Enhanced Drive Agent Tools
class EnhancedDriveTools:
    """Enhanced Drive tools with better error handling."""
    
    def __init__(self):
        self.operations = EnhancedDriveOperations()
    
    async def move_connected_drive_file_enhanced(
        self,
        file_id: str,
        file_name: str,
        destination_folder_id: str,
        current_parent_folder_id: Optional[str] = None
    ) -> str:
        """Enhanced move operation with timeout and error handling."""
        result = await self.operations.move_with_retry(
            file_id, file_name, destination_folder_id, current_parent_folder_id
        )
        
        if result.status == OperationStatus.SUCCESS:
            return f"✅ {result.message}"
        elif result.status == OperationStatus.TIMEOUT:
            return f"⏰ {result.message}. Please try again later."
        elif result.status == OperationStatus.PERMISSION_ERROR:
            return f"🔒 {result.message}. Check if you own this file."
        elif result.status == OperationStatus.PARENT_ERROR:
            return f"📁 {result.message}"
        else:
            return f"❌ {result.message}"
    
    async def batch_move_drive_files(
        self,
        file_moves: List[Dict[str, str]]  # Each dict: {file_id, file_name, destination_folder_id}
    ) -> str:
        """Batch move multiple files with error recovery."""
        # Convert to tuple format
        moves = [
            (move["file_id"], move["file_name"], move["destination_folder_id"])
            for move in file_moves
        ]
        
        results = await self.operations.batch_move_with_recovery(moves)
        
        # Summarize results
        success_count = sum(1 for r in results if r.status == OperationStatus.SUCCESS)
        timeout_count = sum(1 for r in results if r.status == OperationStatus.TIMEOUT)
        error_count = len(results) - success_count - timeout_count
        
        summary = (
            f"Batch move completed:\n"
            f"✅ Success: {success_count}\n"
            f"⏰ Timeouts: {timeout_count}\n"
            f"❌ Errors: {error_count}\n\n"
        )
        
        # Add details for failures
        failures = [r for r in results if r.status != OperationStatus.SUCCESS]
        if failures:
            summary += "Failed items:\n"
            for failure in failures[:10]:  # Limit to 10 items
                summary += f"- {failure.file_name}: {failure.message}\n"
            if len(failures) > 10:
                summary += f"... and {len(failures) - 10} more\n"
        
        return summary

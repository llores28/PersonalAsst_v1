"""Tests for API client tool call conversion (Anthropic and Google)."""

import json
from unittest.mock import MagicMock, AsyncMock, patch

import pytest


class TestAnthropicToolCallConversion:
    """Tests for Anthropic tool call conversion to OpenAI format."""

    @pytest.mark.asyncio
    async def test_converts_tool_use_blocks(self):
        """Test that Anthropic tool_use blocks are converted to OpenAI format."""
        # Mock anthropic module before importing
        mock_anthropic = MagicMock()
        mock_anthropic.types.MessageParam = MagicMock()
        mock_anthropic.types.ToolParam = MagicMock()

        with patch.dict("sys.modules", {"anthropic": mock_anthropic, "anthropic.types": mock_anthropic.types}):
            from src.models.api_clients import call_anthropic_with_tools

            # Create mock Anthropic response
            mock_block = MagicMock()
            mock_block.type = "tool_use"
            mock_block.id = "toolu_01Abc123"
            mock_block.name = "get_weather"
            mock_block.input = {"location": "Boston", "unit": "celsius"}

            mock_response = MagicMock()
            mock_response.content = [mock_block]
            mock_response.stop_reason = "tool_use"
            mock_response.usage.input_tokens = 50
            mock_response.usage.output_tokens = 30
            mock_response.model = "claude-3-5-sonnet"

            # Create mock client
            mock_client = MagicMock()
            mock_client.messages = MagicMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)

            # Call the function
            result = await call_anthropic_with_tools(
                client=mock_client,
                model="claude-3-5-sonnet",
                messages=[{"role": "user", "content": "What's the weather in Boston?"}],
                tools=[{
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Get weather information",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "location": {"type": "string"},
                                "unit": {"type": "string"},
                            },
                        },
                    },
                }],
            )

        # Verify result structure
        assert "choices" in result
        assert len(result["choices"]) == 1

        message = result["choices"][0]["message"]
        assert message["role"] == "assistant"
        assert message["tool_calls"] is not None
        assert len(message["tool_calls"]) == 1

        tool_call = message["tool_calls"][0]
        assert tool_call["id"] == "toolu_01Abc123"
        assert tool_call["type"] == "function"
        assert tool_call["function"]["name"] == "get_weather"
        assert json.loads(tool_call["function"]["arguments"]) == {"location": "Boston", "unit": "celsius"}

        # Verify finish reason
        assert result["choices"][0]["finish_reason"] == "tool_calls"

    @pytest.mark.asyncio
    async def test_handles_mixed_content(self):
        """Test conversion when response has both text and tool_use blocks."""
        mock_anthropic = MagicMock()
        mock_anthropic.types.MessageParam = MagicMock()
        mock_anthropic.types.ToolParam = MagicMock()

        with patch.dict("sys.modules", {"anthropic": mock_anthropic, "anthropic.types": mock_anthropic.types}):
            from src.models.api_clients import call_anthropic_with_tools

            # Create mock blocks - one text, one tool_use
            mock_text_block = MagicMock()
            mock_text_block.type = "text"
            mock_text_block.text = "I'll check the weather for you."

            mock_tool_block = MagicMock()
            mock_tool_block.type = "tool_use"
            mock_tool_block.id = "toolu_02Xyz789"
            mock_tool_block.name = "get_weather"
            mock_tool_block.input = {"location": "Paris"}

            mock_response = MagicMock()
            mock_response.content = [mock_text_block, mock_tool_block]
            mock_response.stop_reason = "tool_use"
            mock_response.usage.input_tokens = 60
            mock_response.usage.output_tokens = 40

            mock_client = MagicMock()
            mock_client.messages = MagicMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)

            result = await call_anthropic_with_tools(
                client=mock_client,
                model="claude-3-5-sonnet",
                messages=[{"role": "user", "content": "What's the weather in Paris?"}],
            )

        # Verify content combines all text blocks
        message = result["choices"][0]["message"]
        assert message["content"] == "I'll check the weather for you."
        assert len(message["tool_calls"]) == 1

    @pytest.mark.asyncio
    async def test_handles_text_only_response(self):
        """Test conversion when response has only text blocks."""
        mock_anthropic = MagicMock()
        mock_anthropic.types.MessageParam = MagicMock()
        mock_anthropic.types.ToolParam = MagicMock()

        with patch.dict("sys.modules", {"anthropic": mock_anthropic, "anthropic.types": mock_anthropic.types}):
            from src.models.api_clients import call_anthropic_with_tools

            mock_block = MagicMock()
            mock_block.type = "text"
            mock_block.text = "The weather in Boston is sunny and 72°F."

            mock_response = MagicMock()
            mock_response.content = [mock_block]
            mock_response.stop_reason = "end_turn"
            mock_response.usage.input_tokens = 40
            mock_response.usage.output_tokens = 20

            mock_client = MagicMock()
            mock_client.messages = MagicMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)

            result = await call_anthropic_with_tools(
                client=mock_client,
                model="claude-3-5-sonnet",
                messages=[{"role": "user", "content": "What's the weather?"}],
            )

        message = result["choices"][0]["message"]
        assert message["content"] == "The weather in Boston is sunny and 72°F."
        assert message["tool_calls"] is None
        assert result["choices"][0]["finish_reason"] == "end_turn"

    @pytest.mark.asyncio
    async def test_handles_empty_input(self):
        """Test conversion when tool has no input arguments."""
        mock_anthropic = MagicMock()
        mock_anthropic.types.MessageParam = MagicMock()
        mock_anthropic.types.ToolParam = MagicMock()

        with patch.dict("sys.modules", {"anthropic": mock_anthropic, "anthropic.types": mock_anthropic.types}):
            from src.models.api_clients import call_anthropic_with_tools

            mock_block = MagicMock()
            mock_block.type = "tool_use"
            mock_block.id = "toolu_03NoInput"
            mock_block.name = "get_status"
            mock_block.input = None

            mock_response = MagicMock()
            mock_response.content = [mock_block]
            mock_response.stop_reason = "tool_use"
            mock_response.usage.input_tokens = 30
            mock_response.usage.output_tokens = 25

            mock_client = MagicMock()
            mock_client.messages = MagicMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)

            result = await call_anthropic_with_tools(
                client=mock_client,
                model="claude-3-5-sonnet",
                messages=[{"role": "user", "content": "Get status"}],
            )

        tool_call = result["choices"][0]["message"]["tool_calls"][0]
        assert json.loads(tool_call["function"]["arguments"]) == {}


class TestGoogleToolCallConversion:
    """Tests for Google Gemini tool call conversion to OpenAI format."""

    @pytest.mark.asyncio
    async def test_converts_function_calls(self):
        """Test that Gemini function calls are converted to OpenAI format."""
        # Mock google.genai module
        mock_genai = MagicMock()
        mock_types = MagicMock()
        mock_genai.types = mock_types

        with patch.dict("sys.modules", {"google": MagicMock(), "google.genai": mock_genai, "google.genai.types": mock_types}):
            from src.models.api_clients import call_google_with_tools

            # Create mock Gemini response with function call
            mock_func_call = MagicMock()
            mock_func_call.name = "calculate_sum"
            mock_func_call.args = {"a": 5, "b": 10}

            mock_part = MagicMock()
            mock_part.text = None
            mock_part.function_call = mock_func_call

            mock_content = MagicMock()
            mock_content.parts = [mock_part]

            mock_candidate = MagicMock()
            mock_candidate.content = mock_content
            mock_candidate.finish_reason = MagicMock()
            mock_candidate.finish_reason.name = "STOP"

            mock_response = MagicMock()
            mock_response.candidates = [mock_candidate]
            mock_response.text = None
            mock_response.usage_metadata = None

            # Create mock client
            mock_client = MagicMock()
            mock_client.aio = MagicMock()
            mock_client.aio.models = MagicMock()
            mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

            # Mock the types
            mock_types.Content = MagicMock(return_value=mock_content)
            mock_types.Part = MagicMock(return_value=mock_part)
            mock_types.Tool = MagicMock()
            mock_types.FunctionDeclaration = MagicMock()
            mock_types.GenerateContentConfig = MagicMock()

            result = await call_google_with_tools(
                client=mock_client,
                model="gemini-1.5-flash",
                messages=[{"role": "user", "content": "Calculate 5 + 10"}],
                tools=[{
                    "type": "function",
                    "function": {
                        "name": "calculate_sum",
                        "description": "Add two numbers",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "a": {"type": "number"},
                                "b": {"type": "number"},
                            },
                        },
                    },
                }],
            )

        # Verify result
        message = result["choices"][0]["message"]
        assert message["tool_calls"] is not None
        assert len(message["tool_calls"]) == 1

        tool_call = message["tool_calls"][0]
        assert tool_call["type"] == "function"
        assert tool_call["function"]["name"] == "calculate_sum"
        assert json.loads(tool_call["function"]["arguments"]) == {"a": 5, "b": 10}

    @pytest.mark.asyncio
    async def test_handles_mixed_text_and_function(self):
        """Test conversion when response has both text and function calls."""
        mock_genai = MagicMock()
        mock_types = MagicMock()
        mock_genai.types = mock_types

        with patch.dict("sys.modules", {"google": MagicMock(), "google.genai": mock_genai, "google.genai.types": mock_types}):
            from src.models.api_clients import call_google_with_tools

            # Create mock parts with both text and function call
            mock_text_part = MagicMock()
            mock_text_part.text = "Let me calculate that for you."
            mock_text_part.function_call = None

            mock_func_part = MagicMock()
            mock_func_part.text = None
            mock_func_call = MagicMock()
            mock_func_call.name = "calculate"
            mock_func_call.args = {"x": 100}
            mock_func_part.function_call = mock_func_call

            mock_content = MagicMock()
            mock_content.parts = [mock_text_part, mock_func_part]

            mock_candidate = MagicMock()
            mock_candidate.content = mock_content
            mock_candidate.finish_reason = MagicMock()
            mock_candidate.finish_reason.name = "STOP"

            mock_response = MagicMock()
            mock_response.candidates = [mock_candidate]
            mock_response.usage_metadata = None

            mock_client = MagicMock()
            mock_client.aio = MagicMock()
            mock_client.aio.models = MagicMock()
            mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

            mock_types.Content = MagicMock(return_value=mock_content)
            mock_types.Part = MagicMock()
            mock_types.GenerateContentConfig = MagicMock()

            result = await call_google_with_tools(
                client=mock_client,
                model="gemini-1.5-flash",
                messages=[{"role": "user", "content": "Calculate 100 squared"}],
            )

        message = result["choices"][0]["message"]
        assert message["content"] == "Let me calculate that for you."
        assert len(message["tool_calls"]) == 1
        assert message["tool_calls"][0]["function"]["name"] == "calculate"

    @pytest.mark.asyncio
    async def test_handles_text_only_response(self):
        """Test conversion when response has only text."""
        mock_genai = MagicMock()
        mock_types = MagicMock()
        mock_genai.types = mock_types

        with patch.dict("sys.modules", {"google": MagicMock(), "google.genai": mock_genai, "google.genai.types": mock_types}):
            from src.models.api_clients import call_google_with_tools

            mock_text_part = MagicMock()
            mock_text_part.text = "The answer is 42."
            mock_text_part.function_call = None

            mock_content = MagicMock()
            mock_content.parts = [mock_text_part]

            mock_candidate = MagicMock()
            mock_candidate.content = mock_content
            mock_candidate.finish_reason = MagicMock()
            mock_candidate.finish_reason.name = "STOP"

            mock_response = MagicMock()
            mock_response.candidates = [mock_candidate]
            mock_response.usage_metadata = None

            mock_client = MagicMock()
            mock_client.aio = MagicMock()
            mock_client.aio.models = MagicMock()
            mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

            mock_types.Content = MagicMock()
            mock_types.Part = MagicMock()
            mock_types.GenerateContentConfig = MagicMock()

            result = await call_google_with_tools(
                client=mock_client,
                model="gemini-1.5-flash",
                messages=[{"role": "user", "content": "What is the answer?"}],
            )

        message = result["choices"][0]["message"]
        assert message["content"] == "The answer is 42."
        assert message["tool_calls"] is None
        assert result["choices"][0]["finish_reason"] == "stop"

    @pytest.mark.asyncio
    async def test_generates_unique_tool_call_ids(self):
        """Test that each function call gets a unique ID."""
        mock_genai = MagicMock()
        mock_types = MagicMock()
        mock_genai.types = mock_types

        with patch.dict("sys.modules", {"google": MagicMock(), "google.genai": mock_genai, "google.genai.types": mock_types}):
            from src.models.api_clients import call_google_with_tools

            # Create multiple function calls
            mock_func1 = MagicMock()
            mock_func1.name = "func1"
            mock_func1.args = {}

            mock_func2 = MagicMock()
            mock_func2.name = "func2"
            mock_func2.args = {}

            mock_part1 = MagicMock()
            mock_part1.text = None
            mock_part1.function_call = mock_func1

            mock_part2 = MagicMock()
            mock_part2.text = None
            mock_part2.function_call = mock_func2

            mock_content = MagicMock()
            mock_content.parts = [mock_part1, mock_part2]

            mock_candidate = MagicMock()
            mock_candidate.content = mock_content
            mock_candidate.finish_reason = MagicMock()
            mock_candidate.finish_reason.name = "STOP"

            mock_response = MagicMock()
            mock_response.candidates = [mock_candidate]
            mock_response.usage_metadata = None

            mock_client = MagicMock()
            mock_client.aio = MagicMock()
            mock_client.aio.models = MagicMock()
            mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

            mock_types.Content = MagicMock()
            mock_types.Part = MagicMock()
            mock_types.GenerateContentConfig = MagicMock()

            result = await call_google_with_tools(
                client=mock_client,
                model="gemini-1.5-flash",
                messages=[{"role": "user", "content": "Call both functions"}],
            )

        tool_calls = result["choices"][0]["message"]["tool_calls"]
        assert len(tool_calls) == 2
        # Verify unique IDs
        assert tool_calls[0]["id"] != tool_calls[1]["id"]
        # Verify ID format
        assert tool_calls[0]["id"].startswith("call_")
        assert len(tool_calls[0]["id"]) == 29  # "call_" + 24 hex chars

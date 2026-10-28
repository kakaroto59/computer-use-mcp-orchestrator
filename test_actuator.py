"""Test suite to validate actuator_core and mcp_daemon functionalities."""
import asyncio
import base64
import io
import sys
from PIL import Image
import actuator_core
from mcp_daemon.server import server


def test_actuator_core_direct():
    print("=== Testing actuator_core native module ===")
    dims = actuator_core.get_screen_dimensions()
    print(f"Screen resolution: {dims[0]}x{dims[1]}")
    assert dims[0] > 0 and dims[1] > 0, "Invalid screen dimensions"

    shot_b64 = actuator_core.get_screenshot("jpeg", 70)
    assert len(shot_b64) > 1000, "Screenshot base64 too short"
    img_data = base64.b64decode(shot_b64)
    img = Image.open(io.BytesIO(img_data))
    print(f"Captured JPEG image dimensions: {img.size}")
    assert img.size == dims, f"Image size {img.size} does not match screen dimensions {dims}"

    actuator_core.mouse_move(dims[0] // 2, dims[1] // 2)
    print("Mouse moved to center successfully.")


async def test_mcp_server_tools():
    print("\n=== Testing MCP Server tools ===")
    tools = await server.list_tools()
    tool_names = [t.name for t in tools]
    print(f"Registered MCP tools ({len(tool_names)}): {tool_names}")
    expected_tools = [
        "capture_screen",
        "execute_click",
        "execute_type",
        "execute_press_key",
        "execute_hotkey",
        "execute_scroll",
        "execute_move",
    ]
    for exp in expected_tools:
        assert exp in tool_names, f"Missing tool: {exp}"

    # Test capture_screen tool
    cap_res = await server.call_tool("capture_screen", {"quality": 60})
    assert len(cap_res.content) == 2, "Expected TextContent and ImageContent"
    print("capture_screen tool returned Text + Image Content correctly.")

    # Test coordinate validation
    err_res = await server.call_tool("execute_click", {"x": -10, "y": -10})
    assert "out of screen bounds" in err_res.content[0].text
    print("execute_click out-of-bounds rejection verified.")

    print("\nAll integration tests passed successfully!")


if __name__ == "__main__":
    test_actuator_core_direct()
    asyncio.run(test_mcp_server_tools())

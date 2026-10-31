"""Test suite to validate actuator_core (with Focus Guard) and mcp_daemon (Normalized Coords)."""
import asyncio
import base64
import io
import json
import numpy as np
from PIL import Image
import actuator_core
from mcp_daemon.server import (
    LATENCY_CLICK_MS,
    LATENCY_HOTKEY_MS,
    LATENCY_TYPE_MS,
    _norm_to_pixel,
    server,
)


def test_actuator_core_direct():
    print("=== Testing actuator_core native module (with Focus Guard) ===")
    dims = actuator_core.get_screen_dimensions()
    print(f"Screen resolution (DPI-aware): {dims[0]}x{dims[1]}")
    assert dims[0] > 0 and dims[1] > 0, "Invalid screen dimensions"

    shot_b64 = actuator_core.get_screenshot("jpeg", 70)
    assert len(shot_b64) > 1000, "Screenshot base64 too short"
    img_data = base64.b64decode(shot_b64)
    img = Image.open(io.BytesIO(img_data))
    print(f"Captured JPEG image dimensions: {img.size}")
    assert img.size == dims, f"Image size {img.size} does not match screen dimensions {dims}"

    actuator_core.mouse_move(dims[0] // 2, dims[1] // 2)
    print("Mouse moved to center successfully.")


def test_vectorized_norm_to_pixel():
    print("\n=== Testing vectorized relative-to-pixel conversion ===")
    w, h = actuator_core.get_screen_dimensions()
    x, y, rw, rh = _norm_to_pixel(0.5, 0.5)
    assert (rw, rh) == (w, h)
    assert x == int(np.rint(w * 0.5)) and y == int(np.rint(h * 0.5))
    print(f"Center (0.5, 0.5) -> ({x}, {y}) on {w}x{h}")

    x_max, y_max, _, _ = _norm_to_pixel(1.0, 1.0)
    assert x_max == w - 1 and y_max == h - 1, f"Boundary clamp failed: {(x_max, y_max)}"
    print(f"Upper bound (1.0, 1.0) -> ({x_max}, {y_max})")


async def test_mcp_server_tools():
    print("\n=== Testing MCP Server tools (Normalized Schema & Latencies) ===")
    tools = await server.list_tools()
    tool_map = {t.name: t for t in tools}
    print(f"Registered MCP tools ({len(tool_map)}): {list(tool_map.keys())}")
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
        assert exp in tool_map, f"Missing tool: {exp}"

    # Verify execute_click schema uses x_rel and y_rel
    click_props = tool_map["execute_click"].input_schema.get("properties", {})
    assert "x_rel" in click_props and "y_rel" in click_props, (
        f"execute_click schema missing x_rel/y_rel: {click_props}"
    )
    assert "x" not in click_props and "y" not in click_props
    print("Verified Pydantic schema for execute_click uses x_rel, y_rel.")

    # Verify reference latencies
    assert LATENCY_TYPE_MS < LATENCY_CLICK_MS < LATENCY_HOTKEY_MS
    print(
        f"Reference latencies verified: type={LATENCY_TYPE_MS}ms, "
        f"click={LATENCY_CLICK_MS}ms, hotkey={LATENCY_HOTKEY_MS}ms"
    )

    # Test capture_screen tool
    cap_res = await server.call_tool("capture_screen", {"quality": 60})
    assert len(cap_res.content) == 2, "Expected TextContent and ImageContent"
    print("capture_screen tool returned Text + Image Content correctly.")

    # Test execute_move with normalized decimal coordinates
    move_res = await server.call_tool("execute_move", {"x_rel": 0.5, "y_rel": 0.5})
    move_meta = json.loads(move_res.content[0].text)
    assert move_meta["status"] == "success"
    assert move_meta["cursor_rel"] == [0.5, 0.5]
    print(f"execute_move(0.5, 0.5) succeeded -> {move_meta['cursor_coords']}")

    # Test execute_click with normalized decimal coordinates (safe bottom-right or center)
    click_res = await server.call_tool(
        "execute_click", {"x_rel": 0.5, "y_rel": 0.5, "button": "left", "delay_ms": 100}
    )
    click_meta = json.loads(click_res.content[0].text)
    assert click_meta["status"] == "success"
    assert click_meta["clicked_rel"] == [0.5, 0.5]
    print(f"execute_click(0.5, 0.5) succeeded -> {click_meta['clicked_coords']}")

    # Test out-of-bounds normalized coordinates rejection (< 0.0 and > 1.0)
    err_res_neg = await server.call_tool("execute_click", {"x_rel": -0.1, "y_rel": 0.5})
    assert "out of screen bounds" in err_res_neg.content[0].text
    err_res_gt = await server.call_tool("execute_click", {"x_rel": 0.5, "y_rel": 1.2})
    assert "out of screen bounds" in err_res_gt.content[0].text
    print("execute_click out-of-bounds [0.0, 1.0] rejection verified.")

    print("\nAll integration tests passed successfully!")


if __name__ == "__main__":
    test_actuator_core_direct()
    test_vectorized_norm_to_pixel()
    asyncio.run(test_mcp_server_tools())

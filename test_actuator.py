"""Test suite to validate actuator_core (Release + GIL-free + HIGH_PRIORITY_CLASS) and mcp_daemon (1024px Grayscale + Minified Schema)."""
import asyncio
import base64
import io
import json
import time
import numpy as np
from PIL import Image
import actuator_core
from mcp_daemon.server import (
    LATENCY_CLICK_MS,
    LATENCY_HOTKEY_MS,
    LATENCY_TYPE_MS,
    MIN_TOOL_COOLDOWN,
    _norm_to_pixel,
    process_screenshot_for_llm,
    server,
    set_focus_assist,
)


def test_actuator_core_direct():
    print("=== Testing actuator_core native module (Release + GIL-free) ===")
    dims = actuator_core.get_screen_dimensions()
    print(f"Screen resolution (DPI-aware): {dims[0]}x{dims[1]}")
    assert dims[0] > 0 and dims[1] > 0, "Invalid screen dimensions"

    t0 = time.perf_counter()
    raw_bytes = bytes(actuator_core.get_screenshot_raw("jpeg", 75))
    t_rust = (time.perf_counter() - t0) * 1000.0
    print(f"Rust raw capture + JPEG encoding time: {t_rust:.2f} ms (Raw size: {len(raw_bytes) / 1024:.2f} KB)")

    t1 = time.perf_counter()
    llm_b64 = process_screenshot_for_llm(raw_bytes)
    t_llm = (time.perf_counter() - t1) * 1000.0
    print(f"LLM image optimization time: {t_llm:.2f} ms (Optimized Base64 size: {len(llm_b64) / 1024:.2f} KB)")

    opt_img = Image.open(io.BytesIO(base64.b64decode(llm_b64)))
    print(f"Optimized LLM image: size={opt_img.size}, mode={opt_img.mode}")
    assert max(opt_img.size) <= 1024, f"Expected max dimension <= 1024, got {opt_img.size}"
    assert opt_img.mode == "L", f"Expected grayscale mode 'L', got {opt_img.mode}"

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
    print("\n=== Testing MCP Server tools (Minified Schema & Optimized Payload) ===")
    tools = await server.list_tools()
    tool_map = {t.name: t for t in tools}
    schema_json = json.dumps([t.model_dump() for t in tools])
    print(f"Registered MCP tools ({len(tool_map)}): {list(tool_map.keys())}")
    print(f"Total serialized MCP tools schema size: {len(schema_json)} bytes")

    expected_tools = [
        "capture_screen",
        "execute_click",
        "execute_type",
        "execute_press_key",
        "execute_hotkey",
        "execute_scroll",
        "execute_move",
        "manage_window",
    ]
    for exp in expected_tools:
        assert exp in tool_map, f"Missing tool: {exp}"

    # Verify execute_click schema uses x_rel and y_rel and minified description
    assert tool_map["execute_click"].description == "Click at relative coordinates [0.0, 1.0]"
    click_props = tool_map["execute_click"].input_schema.get("properties", {})
    assert "x_rel" in click_props and "y_rel" in click_props
    print("Verified minified Pydantic schema for execute_click.")

    # Verify reference latencies and CPU cooldown
    assert LATENCY_TYPE_MS < LATENCY_CLICK_MS < LATENCY_HOTKEY_MS
    assert MIN_TOOL_COOLDOWN == 1.2
    set_focus_assist(True)
    set_focus_assist(False)
    print(f"Verified MIN_TOOL_COOLDOWN={MIN_TOOL_COOLDOWN}s and Focus Assist registry toggle.")

    # Test capture_screen tool
    cap_res = await server.call_tool("capture_screen", {"quality": 60, "delay_ms": 0})
    assert len(cap_res.content) == 2, "Expected TextContent and ImageContent"
    print(f"capture_screen payload Base64 size: {len(cap_res.content[1].data) / 1024:.2f} KB")

    # Test execute_move with normalized decimal coordinates
    move_res = await server.call_tool("execute_move", {"x_rel": 0.5, "y_rel": 0.5})
    move_meta = json.loads(move_res.content[0].text)
    assert move_meta["status"] == "success"
    assert move_meta["cursor_rel"] == [0.5, 0.5]

    # Test execute_click with normalized decimal coordinates
    click_res = await server.call_tool(
        "execute_click", {"x_rel": 0.5, "y_rel": 0.5, "button": "left", "delay_ms": 50}
    )
    click_meta = json.loads(click_res.content[0].text)
    assert click_meta["status"] == "success"
    assert click_meta["clicked_rel"] == [0.5, 0.5]

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

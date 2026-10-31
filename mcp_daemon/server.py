import ctypes
import json
import logging
import sys
import time
from typing import Annotated, Literal

import numpy as np
from mcp.server.mcpserver import MCPServer
from mcp.types import ImageContent, TextContent
from pydantic import Field
import actuator_core

try:
    ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

# Configure logging to stderr to prevent interference with stdio MCP JSON-RPC
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s [%(name)s]: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("mcp_daemon")

# Anthropomorphic / Anthropic computer-use-demo reference latencies (ms)
LATENCY_CAPTURE_MS = 100
LATENCY_TYPE_MS = 200
LATENCY_KEY_MS = 300
LATENCY_SCROLL_MS = 350
LATENCY_CLICK_MS = 500
LATENCY_HOTKEY_MS = 600

_last_screen_dims: np.ndarray | None = None

server = MCPServer(
    name="ComputerUseActuator",
    instructions=(
        "Native OS automation daemon for computer use via Rust actuator (with Focus Guard) "
        "and MCP using normalized coordinates [0.0, 1.0]."
    ),
    version="0.2.0",
)


def _get_screen_dims() -> tuple[int, int]:
    """Retrieve and cache current screen dimensions."""
    global _last_screen_dims
    w, h = actuator_core.get_screen_dimensions()
    _last_screen_dims = np.array([w, h], dtype=np.float64)
    return int(w), int(h)


def _norm_to_pixel(x_rel: float, y_rel: float) -> tuple[int, int, int, int]:
    """Vectorized conversion from normalized [0.0, 1.0] coordinates to exact screen pixels."""
    coords_rel = np.array([x_rel, y_rel], dtype=np.float64)
    if np.any(np.isnan(coords_rel)) or np.any((coords_rel < 0.0) | (coords_rel > 1.0)):
        raise ValueError(
            f"Normalized coordinates ({x_rel}, {y_rel}) out of screen bounds [0.0, 1.0]"
        )

    w, h = _get_screen_dims()
    dims = np.array([w, h], dtype=np.float64)
    px = np.clip(np.rint(dims * coords_rel), 0, dims - 1).astype(int)
    return int(px[0]), int(px[1]), w, h


def _get_feedback_payload(
    action: str,
    extra_meta: dict,
    delay_ms: int = LATENCY_CLICK_MS,
    quality: int = 75,
) -> list:
    """Waits for the OS rendering cycle, captures screen, and constructs MCP payload."""
    if delay_ms > 0:
        time.sleep(delay_ms / 1000.0)

    try:
        width, height = _get_screen_dims()
        screenshot_b64 = actuator_core.get_screenshot("jpeg", quality)
        meta = {
            "status": "success",
            "action": action,
            "screen_width": width,
            "screen_height": height,
            "screenshot_format": "jpeg",
            "delay_ms": delay_ms,
            **extra_meta,
        }
        return [
            TextContent(type="text", text=json.dumps(meta, indent=2)),
            ImageContent(
                type="image",
                data=screenshot_b64,
                mime_type="image/jpeg",
            ),
        ]
    except Exception as exc:
        logger.error(f"Feedback capture failed: {exc}", exc_info=True)
        error_meta = {
            "status": "partial_success",
            "action": action,
            "error": f"Action executed, but post-capture failed: {str(exc)}",
            **extra_meta,
        }
        return [TextContent(type="text", text=json.dumps(error_meta, indent=2))]


@server.tool()
def capture_screen(
    format: Literal["jpeg", "png"] = "jpeg",
    quality: int = 75,
    delay_ms: int = LATENCY_CAPTURE_MS,
) -> list:
    """Capture the current display state and update resolution reference.

    Args:
        format: Image compression format ('jpeg' or 'png').
        quality: JPEG compression quality (1-100). Ignored for PNG.
        delay_ms: Delay in milliseconds before taking screenshot.
    """
    logger.info(f"Tool call: capture_screen(format={format}, quality={quality})")
    if delay_ms > 0:
        time.sleep(delay_ms / 1000.0)

    try:
        width, height = _get_screen_dims()
        b64 = actuator_core.get_screenshot(format, max(1, min(100, quality)))
        mime_type = "image/png" if format == "png" else "image/jpeg"
        meta = {
            "status": "success",
            "action": "capture_screen",
            "screen_width": width,
            "screen_height": height,
            "format": format,
        }
        return [
            TextContent(type="text", text=json.dumps(meta, indent=2)),
            ImageContent(type="image", data=b64, mime_type=mime_type),
        ]
    except Exception as exc:
        logger.error(f"capture_screen failed: {exc}", exc_info=True)
        return [
            TextContent(
                type="text",
                text=json.dumps(
                    {"status": "error", "action": "capture_screen", "message": str(exc)},
                    indent=2,
                ),
            )
        ]


@server.tool()
def execute_click(
    x_rel: Annotated[
        float,
        Field(description="Normalized horizontal coordinate in [0.0, 1.0]."),
    ],
    y_rel: Annotated[
        float,
        Field(description="Normalized vertical coordinate in [0.0, 1.0]."),
    ],
    button: Literal["left", "right", "middle"] = "left",
    delay_ms: int = LATENCY_CLICK_MS,
) -> list:
    """Move cursor to normalized (x_rel, y_rel), enforce focus, execute mouse click, and return validation screenshot.

    Args:
        x_rel: Target normalized horizontal coordinate (0.0 to 1.0).
        y_rel: Target normalized vertical coordinate (0.0 to 1.0).
        button: Mouse button ('left', 'right', 'middle').
        delay_ms: Latency to wait after click for visual feedback before screenshot (default 500ms).
    """
    logger.info(f"Tool call: execute_click(x_rel={x_rel}, y_rel={y_rel}, button={button})")
    try:
        x, y, width, height = _norm_to_pixel(x_rel, y_rel)
        actuator_core.click_at(x, y, button)
        return _get_feedback_payload(
            action="execute_click",
            extra_meta={
                "clicked_rel": [x_rel, y_rel],
                "clicked_coords": [x, y],
                "button": button,
            },
            delay_ms=delay_ms,
        )
    except ValueError as val_err:
        return [
            TextContent(
                type="text",
                text=json.dumps(
                    {
                        "status": "error",
                        "action": "execute_click",
                        "message": str(val_err),
                    },
                    indent=2,
                ),
            )
        ]
    except Exception as exc:
        logger.error(f"execute_click failed: {exc}", exc_info=True)
        return [
            TextContent(
                type="text",
                text=json.dumps(
                    {"status": "error", "action": "execute_click", "message": str(exc)},
                    indent=2,
                ),
            )
        ]


@server.tool()
def execute_type(
    text: str,
    press_enter: bool = False,
    delay_ms: int = LATENCY_TYPE_MS,
) -> list:
    """Type text into current focused element (protected by Focus Guard), optionally pressing Enter, and return feedback screenshot.

    Args:
        text: Text string to type.
        press_enter: If True, automatically sends Enter key after typing text.
        delay_ms: Latency to wait after typing for visual feedback before screenshot (default 200ms).
    """
    logger.info(f"Tool call: execute_type(len={len(text)}, press_enter={press_enter})")
    try:
        actuator_core.type_text(text)
        if press_enter:
            actuator_core.press_key("enter")
            effective_delay = max(delay_ms, LATENCY_CLICK_MS)
        else:
            effective_delay = delay_ms

        return _get_feedback_payload(
            action="execute_type",
            extra_meta={"typed_length": len(text), "press_enter": press_enter},
            delay_ms=effective_delay,
        )
    except Exception as exc:
        logger.error(f"execute_type failed: {exc}", exc_info=True)
        return [
            TextContent(
                type="text",
                text=json.dumps(
                    {"status": "error", "action": "execute_type", "message": str(exc)},
                    indent=2,
                ),
            )
        ]


@server.tool()
def execute_press_key(
    key: str,
    delay_ms: int = LATENCY_KEY_MS,
) -> list:
    """Press a single key (e.g. 'enter', 'tab', 'escape', 'space', 'backspace', 'up', 'down', 'left', 'right') and return feedback screenshot.

    Args:
        key: Name of the key to press.
        delay_ms: Latency to wait after pressing key before screenshot (default 300ms).
    """
    logger.info(f"Tool call: execute_press_key(key={key})")
    try:
        actuator_core.press_key(key)
        return _get_feedback_payload(
            action="execute_press_key",
            extra_meta={"pressed_key": key},
            delay_ms=delay_ms,
        )
    except Exception as exc:
        logger.error(f"execute_press_key failed: {exc}", exc_info=True)
        return [
            TextContent(
                type="text",
                text=json.dumps(
                    {"status": "error", "action": "execute_press_key", "message": str(exc)},
                    indent=2,
                ),
            )
        ]


@server.tool()
def execute_hotkey(
    keys: list[str],
    delay_ms: int = LATENCY_HOTKEY_MS,
) -> list:
    """Press a keyboard combination (e.g. ['win', 'r'], ['ctrl', 'c'], ['alt', 'tab']) and return feedback screenshot.

    Args:
        keys: List of key names in combination sequence.
        delay_ms: Latency to wait after executing hotkey before screenshot (default 600ms for menu/window transitions).
    """
    logger.info(f"Tool call: execute_hotkey(keys={keys})")
    try:
        actuator_core.hotkey(keys)
        return _get_feedback_payload(
            action="execute_hotkey",
            extra_meta={"hotkey": keys},
            delay_ms=delay_ms,
        )
    except Exception as exc:
        logger.error(f"execute_hotkey failed: {exc}", exc_info=True)
        return [
            TextContent(
                type="text",
                text=json.dumps(
                    {"status": "error", "action": "execute_hotkey", "message": str(exc)},
                    indent=2,
                ),
            )
        ]


@server.tool()
def execute_scroll(
    direction: Literal["up", "down", "left", "right"] = "down",
    amount: int = 3,
    delay_ms: int = LATENCY_SCROLL_MS,
) -> list:
    """Scroll mouse wheel in specified direction and return feedback screenshot.

    Args:
        direction: Direction of scroll ('up', 'down', 'left', 'right').
        amount: Number of scroll increments.
        delay_ms: Latency to wait after scroll before screenshot (default 350ms).
    """
    logger.info(f"Tool call: execute_scroll(direction={direction}, amount={amount})")
    try:
        actuator_core.scroll(direction, amount)
        return _get_feedback_payload(
            action="execute_scroll",
            extra_meta={"direction": direction, "amount": amount},
            delay_ms=delay_ms,
        )
    except Exception as exc:
        logger.error(f"execute_scroll failed: {exc}", exc_info=True)
        return [
            TextContent(
                type="text",
                text=json.dumps(
                    {"status": "error", "action": "execute_scroll", "message": str(exc)},
                    indent=2,
                ),
            )
        ]


@server.tool()
def execute_move(
    x_rel: Annotated[
        float,
        Field(description="Normalized horizontal coordinate in [0.0, 1.0]."),
    ],
    y_rel: Annotated[
        float,
        Field(description="Normalized vertical coordinate in [0.0, 1.0]."),
    ],
) -> list:
    """Move mouse cursor to normalized (x_rel, y_rel) without clicking.

    Args:
        x_rel: Target normalized horizontal coordinate (0.0 to 1.0).
        y_rel: Target normalized vertical coordinate (0.0 to 1.0).
    """
    logger.info(f"Tool call: execute_move(x_rel={x_rel}, y_rel={y_rel})")
    try:
        x, y, width, height = _norm_to_pixel(x_rel, y_rel)
        actuator_core.mouse_move(x, y)
        return [
            TextContent(
                type="text",
                text=json.dumps(
                    {
                        "status": "success",
                        "action": "execute_move",
                        "cursor_rel": [x_rel, y_rel],
                        "cursor_coords": [x, y],
                    },
                    indent=2,
                ),
            )
        ]
    except ValueError as val_err:
        return [
            TextContent(
                type="text",
                text=json.dumps(
                    {
                        "status": "error",
                        "action": "execute_move",
                        "message": str(val_err),
                    },
                    indent=2,
                ),
            )
        ]
    except Exception as exc:
        logger.error(f"execute_move failed: {exc}", exc_info=True)
        return [
            TextContent(
                type="text",
                text=json.dumps(
                    {"status": "error", "action": "execute_move", "message": str(exc)},
                    indent=2,
                ),
            )
        ]


def main():
    logger.info("Starting Computer Use MCP Server v0.2.0 on stdio transport...")
    server.run(transport="stdio")


if __name__ == "__main__":
    main()

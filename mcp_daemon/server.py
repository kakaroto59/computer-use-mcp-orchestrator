import json
import logging
import sys
import time
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ImageContent, TextContent
import actuator_core

# Configure logging to stderr to prevent interference with stdio MCP JSON-RPC
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s [%(name)s]: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("mcp_daemon")

server = MCPServer(
    name="ComputerUseActuator",
    instructions="Native OS automation daemon for computer use via Rust actuator and MCP.",
    version="0.1.0",
)


def _get_feedback_payload(
    action: str,
    extra_meta: dict,
    delay_ms: int = 350,
    quality: int = 75,
) -> list:
    """Waits for the OS rendering cycle, captures screen, and constructs MCP payload."""
    if delay_ms > 0:
        time.sleep(delay_ms / 1000.0)

    try:
        width, height = actuator_core.get_screen_dimensions()
        screenshot_b64 = actuator_core.get_screenshot("jpeg", quality)
        meta = {
            "status": "success",
            "action": action,
            "screen_width": width,
            "screen_height": height,
            "screenshot_format": "jpeg",
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
    delay_ms: int = 100,
) -> list:
    """Capture the current display state.

    Args:
        format: Image compression format ('jpeg' or 'png').
        quality: JPEG compression quality (1-100). Ignored for PNG.
        delay_ms: Delay in milliseconds before taking screenshot.
    """
    logger.info(f"Tool call: capture_screen(format={format}, quality={quality})")
    if delay_ms > 0:
        time.sleep(delay_ms / 1000.0)

    try:
        width, height = actuator_core.get_screen_dimensions()
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
    x: int,
    y: int,
    button: Literal["left", "right", "middle"] = "left",
    delay_ms: int = 350,
) -> list:
    """Move cursor to (x, y), execute mouse click, and return validation screenshot.

    Args:
        x: Target horizontal coordinate in pixels.
        y: Target vertical coordinate in pixels.
        button: Mouse button ('left', 'right', 'middle').
        delay_ms: Latency to wait after click for visual feedback before screenshot.
    """
    logger.info(f"Tool call: execute_click(x={x}, y={y}, button={button})")
    try:
        width, height = actuator_core.get_screen_dimensions()
        if x < 0 or x >= width or y < 0 or y >= height:
            return [
                TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "status": "error",
                            "action": "execute_click",
                            "message": f"Coordinates ({x}, {y}) out of screen bounds ({width}x{height})",
                        },
                        indent=2,
                    ),
                )
            ]

        actuator_core.click_at(x, y, button)
        return _get_feedback_payload(
            action="execute_click",
            extra_meta={"clicked_coords": [x, y], "button": button},
            delay_ms=delay_ms,
        )
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
    delay_ms: int = 350,
) -> list:
    """Type text into current focused element, optionally pressing Enter, and return feedback screenshot.

    Args:
        text: Text string to type.
        press_enter: If True, automatically sends Enter key after typing text.
        delay_ms: Latency to wait after typing for visual feedback before screenshot.
    """
    logger.info(f"Tool call: execute_type(len={len(text)}, press_enter={press_enter})")
    try:
        actuator_core.type_text(text)
        if press_enter:
            actuator_core.press_key("enter")

        return _get_feedback_payload(
            action="execute_type",
            extra_meta={"typed_length": len(text), "press_enter": press_enter},
            delay_ms=delay_ms,
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
    delay_ms: int = 350,
) -> list:
    """Press a single key (e.g. 'enter', 'tab', 'escape', 'space', 'backspace', 'up', 'down', 'left', 'right') and return feedback screenshot.

    Args:
        key: Name of the key to press.
        delay_ms: Latency to wait after pressing key before screenshot.
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
    delay_ms: int = 350,
) -> list:
    """Press a keyboard combination (e.g. ['win', 'r'], ['ctrl', 'c'], ['alt', 'tab']) and return feedback screenshot.

    Args:
        keys: List of key names in combination sequence.
        delay_ms: Latency to wait after executing hotkey before screenshot.
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
    delay_ms: int = 350,
) -> list:
    """Scroll mouse wheel in specified direction and return feedback screenshot.

    Args:
        direction: Direction of scroll ('up', 'down', 'left', 'right').
        amount: Number of scroll increments.
        delay_ms: Latency to wait after scroll before screenshot.
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
    x: int,
    y: int,
) -> list:
    """Move mouse cursor to (x, y) without clicking.

    Args:
        x: Target horizontal coordinate.
        y: Target vertical coordinate.
    """
    logger.info(f"Tool call: execute_move(x={x}, y={y})")
    try:
        width, height = actuator_core.get_screen_dimensions()
        if x < 0 or x >= width or y < 0 or y >= height:
            return [
                TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "status": "error",
                            "action": "execute_move",
                            "message": f"Coordinates ({x}, {y}) out of screen bounds ({width}x{height})",
                        },
                        indent=2,
                    ),
                )
            ]

        actuator_core.mouse_move(x, y)
        return [
            TextContent(
                type="text",
                text=json.dumps(
                    {
                        "status": "success",
                        "action": "execute_move",
                        "cursor_coords": [x, y],
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
    logger.info("Starting Computer Use MCP Server on stdio transport...")
    server.run(transport="stdio")


if __name__ == "__main__":
    main()

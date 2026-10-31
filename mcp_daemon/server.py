import base64
import ctypes
from io import BytesIO
import json
import logging
import sys
import time
from typing import Annotated, Literal

import numpy as np
from mcp.server.mcpserver import MCPServer
from mcp.types import ImageContent, TextContent
from PIL import Image
from pydantic import Field
import actuator_core

try:
    ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s [%(name)s]: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("mcp_daemon")

LATENCY_CAPTURE_MS = 100
LATENCY_TYPE_MS = 200
LATENCY_KEY_MS = 300
LATENCY_SCROLL_MS = 350
LATENCY_CLICK_MS = 500
LATENCY_HOTKEY_MS = 600

_last_screen_dims: np.ndarray | None = None

server = MCPServer(
    name="ComputerUseActuator",
    instructions="OS automation via normalized coordinates [0.0, 1.0].",
    version="0.3.0",
)


def process_screenshot_for_llm(raw_img_bytes: bytes) -> str:
    """Downscale to max 1024px, convert to grayscale, and compress as JPEG Base64."""
    with Image.open(BytesIO(raw_img_bytes)) as img:
        img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        img = img.convert("L")
        buffer = BytesIO()
        img.save(buffer, format="JPEG", quality=60, optimize=True)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")


def _capture_optimized_b64(format: str = "jpeg", quality: int = 75) -> str:
    """Capture screen via Rust GIL-free call and apply LLM token optimization."""
    if hasattr(actuator_core, "get_screenshot_raw"):
        raw_bytes = bytes(actuator_core.get_screenshot_raw(format, max(1, min(100, quality))))
    else:
        b64_raw = actuator_core.get_screenshot(format, max(1, min(100, quality)))
        raw_bytes = base64.b64decode(b64_raw)
    return process_screenshot_for_llm(raw_bytes)


def _get_screen_dims() -> tuple[int, int]:
    global _last_screen_dims
    w, h = actuator_core.get_screen_dimensions()
    _last_screen_dims = np.array([w, h], dtype=np.float64)
    return int(w), int(h)


def _norm_to_pixel(x_rel: float, y_rel: float) -> tuple[int, int, int, int]:
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
    if delay_ms > 0:
        time.sleep(delay_ms / 1000.0)

    try:
        width, height = _get_screen_dims()
        screenshot_b64 = _capture_optimized_b64("jpeg", quality)
        meta = {
            "status": "success",
            "action": action,
            "screen_width": width,
            "screen_height": height,
            "delay_ms": delay_ms,
            **extra_meta,
        }
        return [
            TextContent(type="text", text=json.dumps(meta, separators=(",", ":"))),
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
            "error": str(exc),
            **extra_meta,
        }
        return [TextContent(type="text", text=json.dumps(error_meta, separators=(",", ":")))]


@server.tool()
def capture_screen(
    format: Literal["jpeg", "png"] = "jpeg",
    quality: int = 75,
    delay_ms: int = LATENCY_CAPTURE_MS,
) -> list:
    """Capture screen"""
    logger.info(f"Tool call: capture_screen(format={format}, quality={quality})")
    if delay_ms > 0:
        time.sleep(delay_ms / 1000.0)

    try:
        width, height = _get_screen_dims()
        b64 = _capture_optimized_b64(format, quality)
        meta = {
            "status": "success",
            "action": "capture_screen",
            "screen_width": width,
            "screen_height": height,
        }
        return [
            TextContent(type="text", text=json.dumps(meta, separators=(",", ":"))),
            ImageContent(type="image", data=b64, mime_type="image/jpeg"),
        ]
    except Exception as exc:
        logger.error(f"capture_screen failed: {exc}", exc_info=True)
        return [
            TextContent(
                type="text",
                text=json.dumps(
                    {"status": "error", "action": "capture_screen", "message": str(exc)},
                    separators=(",", ":"),
                ),
            )
        ]


@server.tool()
def execute_click(
    x_rel: Annotated[float, Field(description="X [0.0, 1.0]")],
    y_rel: Annotated[float, Field(description="Y [0.0, 1.0]")],
    button: Literal["left", "right", "middle"] = "left",
    delay_ms: int = LATENCY_CLICK_MS,
) -> list:
    """Click at relative coordinates [0.0, 1.0]"""
    logger.info(f"Tool call: execute_click(x_rel={x_rel}, y_rel={y_rel}, button={button})")
    try:
        x, y, _, _ = _norm_to_pixel(x_rel, y_rel)
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
                    {"status": "error", "action": "execute_click", "message": str(val_err)},
                    separators=(",", ":"),
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
                    separators=(",", ":"),
                ),
            )
        ]


@server.tool()
def execute_type(
    text: str,
    press_enter: bool = False,
    delay_ms: int = LATENCY_TYPE_MS,
) -> list:
    """Type string"""
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
                    separators=(",", ":"),
                ),
            )
        ]


@server.tool()
def execute_press_key(
    key: str,
    delay_ms: int = LATENCY_KEY_MS,
) -> list:
    """Press single key"""
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
                    separators=(",", ":"),
                ),
            )
        ]


@server.tool()
def execute_hotkey(
    keys: list[str],
    delay_ms: int = LATENCY_HOTKEY_MS,
) -> list:
    """Press key combination"""
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
                    separators=(",", ":"),
                ),
            )
        ]


@server.tool()
def execute_scroll(
    direction: Literal["up", "down", "left", "right"] = "down",
    amount: int = 3,
    delay_ms: int = LATENCY_SCROLL_MS,
) -> list:
    """Scroll mouse wheel"""
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
                    separators=(",", ":"),
                ),
            )
        ]


@server.tool()
def execute_move(
    x_rel: Annotated[float, Field(description="X [0.0, 1.0]")],
    y_rel: Annotated[float, Field(description="Y [0.0, 1.0]")],
) -> list:
    """Move cursor to relative coordinates [0.0, 1.0]"""
    logger.info(f"Tool call: execute_move(x_rel={x_rel}, y_rel={y_rel})")
    try:
        x, y, _, _ = _norm_to_pixel(x_rel, y_rel)
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
                    separators=(",", ":"),
                ),
            )
        ]
    except ValueError as val_err:
        return [
            TextContent(
                type="text",
                text=json.dumps(
                    {"status": "error", "action": "execute_move", "message": str(val_err)},
                    separators=(",", ":"),
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
                    separators=(",", ":"),
                ),
            )
        ]


def main():
    logger.info("Starting Computer Use MCP Server v0.3.0 on stdio transport...")
    server.run(transport="stdio")


if __name__ == "__main__":
    main()

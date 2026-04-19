"""
Claude Watch MCP server.

Exposes the M5StickC Plus watch as MCP tools so Claude can:
  - push status updates to the watch display
  - send notifications with buzz + LED flash
  - block on a yes/no approval from the user
  - read any pending gesture / button events
"""

from mcp.server.fastmcp import FastMCP

from .ble_bridge import BLEBridge

bridge = BLEBridge()
mcp = FastMCP("claude-watch")


@mcp.tool()
def set_watch_status(status: str) -> str:
    """
    Update the status line on the watch display. Call this to keep the user
    informed about what Claude is currently doing (e.g. "Reading files…",
    "Running tests…", "Done").
    """
    success = bridge.send(f"S:{status[:20]}")
    return "ok" if success else "watch not connected"


@mcp.tool()
def notify_watch(message: str) -> str:
    """
    Send a notification to the watch: buzzes twice, flashes the LED, and
    shows the message for 3 seconds. Use when Claude finishes a task or
    needs the user's attention without requiring a response.
    """
    success = bridge.send(f"N:{message[:20]}")
    return "ok" if success else "watch not connected"


@mcp.tool()
def ask_watch(question: str, timeout_seconds: int = 30) -> str:
    """
    Show a yes/no question on the watch and wait for the user to respond.
    BTN_A (large button) = YES / approve.
    BTN_B (small button) = NO / reject.
    Returns "approved", "rejected", or "timeout".
    Use before taking any irreversible or high-impact action.
    """
    if not bridge.connected:
        return "watch not connected"

    # Clear stale events so we don't accidentally consume an old button press
    bridge.drain_events()

    if not bridge.send(f"A:{question[:20]}"):
        return "watch not connected"

    result = bridge.wait_for_approval(timeout=float(timeout_seconds))
    if result == "APPROVE":
        return "approved"
    if result == "REJECT":
        return "rejected"
    return "timeout"


@mcp.tool()
def get_watch_events() -> list[str]:
    """
    Return all pending events from the watch since the last call (button
    presses and gestures). Useful for checking whether the user has issued
    any input before deciding what to do next.

    Event names: BTN_A, BTN_B, SHAKE, FLICK_FORWARD, FLICK_BACK,
    ROTATE_CW, ROTATE_CCW, TILT_UP, TILT_DOWN, TILT_LEFT, TILT_RIGHT.
    """
    return bridge.drain_events()


@mcp.tool()
def watch_connected() -> bool:
    """Return True if the M5StickC Plus watch is currently connected."""
    return bridge.connected


def run() -> None:
    bridge.start()
    mcp.run()

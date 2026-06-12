"""FlightScnr visual theme — round visible area on any panel resolution."""

REF_SIZE = 390

try:
    from config import DISPLAY_WIDTH, DISPLAY_HEIGHT
except ImportError:
    DISPLAY_WIDTH = 1080
    DISPLAY_HEIGHT = 1080

# Square reference used for scaling (round panel diameter).
SIZE = min(DISPLAY_WIDTH, DISPLAY_HEIGHT)
SCALE = SIZE / REF_SIZE

# Round viewport: centered on the framebuffer; only the circle shows through the bezel.
CENTER_X = DISPLAY_WIDTH // 2
CENTER_Y = DISPLAY_HEIGHT // 2


def s(value: float) -> int:
    return max(1, int(round(value * SCALE)))


BEZEL_INSET = s(10)
VISIBLE_RADIUS = SIZE // 2 - BEZEL_INSET

# Colors (FlightScnr radar_theme.h)
BG = (2, 15, 3)
GRID = (16, 100, 32)
PAGE_DOT_INACTIVE = (8, 42, 14)
CROSSHAIR = GRID
SWEEP = (48, 255, 96)
SWEEP_TRAIL = (12, 72, 28)
LABEL = (255, 255, 255)
AIRCRAFT = (255, 180, 40)
TAG_TYPE = (255, 200, 0)
TAG_ALT_ASCEND = (0, 255, 255)
TAG_ALT_DESCEND = (255, 0, 255)
HINT = (120, 140, 160)
MUTED = (180, 200, 220)
ROUTE = (100, 220, 255)
LIVE = (56, 168, 255)
LIVE_DIM = (12, 42, 80)

GRID_OUTER_RADIUS = min(s(174), VISIBLE_RADIUS - s(16))
CARDINAL_NORTH_OFFSET_Y = s(10)
CARDINAL_SOUTH_OFFSET_Y = s(10)
CARDINAL_DIAGONAL_INSET = s(6)
SCALE_GAP_FROM_OUTER_RING = s(12)
SCALE_GAP_OUTER_RING_KM = s(20)
SCALE_LABEL_BEARING_DEG = 245.5
RING_COUNT = 3
GRID_DASH_LEN = s(7)
GRID_DASH_GAP = s(15)
AIRCRAFT_ICON_RADIUS = s(15)
AIRCRAFT_LABEL_GAP = s(3)
BEYOND_RING_MARGIN = s(3)
SWEEP_PERIOD_MS = 6000
SWEEP_FRAME_MS = 33
SWEEP_RADIUS = VISIBLE_RADIUS - BEYOND_RING_MARGIN
TAP_PICK_RADIUS = s(36)

FONT_TITLE = s(28)
FONT_BODY = s(22)
FONT_DETAIL = s(18)
FONT_CLOCK = s(64)
FONT_CLOCK_AMPM = s(36)
FONT_CARDINAL = s(23)
FONT_TAG = s(21)
FONT_TAG_SUB = s(17)


def in_visible_circle(x: float, y: float, margin: float = 0) -> bool:
    dx = x - CENTER_X
    dy = y - CENTER_Y
    limit = VISIBLE_RADIUS - margin
    return dx * dx + dy * dy <= limit * limit

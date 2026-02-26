from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
import threading
import time
from typing import Callable, Optional

try:
    import pyautogui
except Exception:  # pragma: no cover
    pyautogui = None

try:
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None

try:
    import pygetwindow as gw
except Exception:  # pragma: no cover
    gw = None


LogCallback = Callable[[str], None]
StatusCallback = Callable[[str, str], None]
FinishCallback = Callable[[bool, str], None]


def _resource_base_dir() -> Path:
    """Resolve resource root for source runs and PyInstaller builds."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent


@dataclass
class BotSettings:
    target_name: str = "Ashgor"
    auto_focus: bool = False
    safety_checks: bool = True
    dry_run: bool = True
    loop_delay_seconds: float = 0.7
    runtime_limit_enabled: bool = False
    runtime_limit_minutes: float = 10.0
    loop_limit_enabled: bool = False
    loop_limit_count: int = 10


class GameBot:
    """Simple automation worker with cooperative stop and UI callbacks."""

    def __init__(
        self,
        settings: Optional[BotSettings] = None,
        on_log: Optional[LogCallback] = None,
        on_status: Optional[StatusCallback] = None,
        on_finished: Optional[FinishCallback] = None,
    ):
        self.settings = settings or BotSettings()
        self.on_log = on_log
        self.on_status = on_status
        self.on_finished = on_finished

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._running = False
        self._play_button_pos: Optional[tuple[int, int]] = None
        self._ashgor_body_missing_warned = False
        self._resource_dir = _resource_base_dir()
        self._roblox_window_titles = ("Roblox", "Roblox Player", "RobloxPlayerBeta")
        self._chrome_window_titles = ("Google Chrome", "Chrome", "Google")

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def start(self) -> bool:
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._stop_event.clear()

        self._thread = threading.Thread(target=self._run, name="GameBotThread", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> bool:
        with self._lock:
            if not self._running:
                return False
            self._stop_event.set()
        return True

    def _emit_log(self, message: str) -> None:
        if self.on_log:
            self.on_log(message)

    def _emit_status(self, text: str, color: str) -> None:
        if self.on_status:
            self.on_status(text, color)

    def _emit_finished(self, success: bool, reason: str) -> None:
        if self.on_finished:
            self.on_finished(success, reason)

    def _sleep_interruptible(self, seconds: float) -> bool:
        end = time.time() + seconds
        while time.time() < end:
            if self._stop_event.is_set():
                return False
            time.sleep(0.05)
        return True

    @staticmethod
    def _is_not_found_exc(exc: Exception) -> bool:
        return exc.__class__.__name__ == "ImageNotFoundException"

    @staticmethod
    def _is_failsafe_exc(exc: Exception) -> bool:
        name = exc.__class__.__name__.lower()
        msg = str(exc).lower()
        return ("failsafe" in name) or ("fail-safe" in msg) or ("failsafe" in msg)

    def _recover_mouse_from_corner(self) -> bool:
        if pyautogui is None:
            return False
        try:
            sw, sh = pyautogui.size()
            target_x = max(1, sw // 2)
            target_y = max(1, sh // 2)

            prev_failsafe = getattr(pyautogui, "FAILSAFE", None)
            try:
                if prev_failsafe is not None:
                    pyautogui.FAILSAFE = False
                pyautogui.moveTo(target_x, target_y, duration=0.0)
            finally:
                if prev_failsafe is not None:
                    pyautogui.FAILSAFE = prev_failsafe

            self._emit_log(f"Recovered mouse from fail-safe corner to ({target_x}, {target_y}).")
            return True
        except Exception as exc:
            self._emit_log(f"Failed to recover mouse from fail-safe corner: {exc}")
            return False

    def _move_mouse_to_center(self, region=None) -> bool:
        if pyautogui is None:
            return False
        try:
            if region:
                target_x = int(region[0] + (region[2] // 2))
                target_y = int(region[1] + (region[3] // 2))
            else:
                sw, sh = pyautogui.size()
                target_x = int(sw // 2)
                target_y = int(sh // 2)

            prev_failsafe = getattr(pyautogui, "FAILSAFE", None)
            try:
                if prev_failsafe is not None:
                    pyautogui.FAILSAFE = False
                pyautogui.moveTo(target_x, target_y, duration=0.0)
            finally:
                if prev_failsafe is not None:
                    pyautogui.FAILSAFE = prev_failsafe

            self._emit_log(f"Moved mouse to center ({target_x}, {target_y}) for camera alignment.")
            return True
        except Exception as exc:
            self._emit_log(f"Failed to move mouse to center for alignment: {exc}")
            return False

    def _find_first_window(self, candidate_titles: tuple[str, ...]):
        if gw is None:
            return None
        for title in candidate_titles:
            try:
                windows = gw.getWindowsWithTitle(title)
            except Exception:
                continue
            if not windows:
                continue
            for win in windows:
                try:
                    win_title = getattr(win, "title", "")
                except Exception:
                    win_title = ""
                if isinstance(win_title, str) and not win_title.strip():
                    continue
                return win
        return None

    def _focus_roblox_window(self) -> bool:
        if gw is None:
            self._emit_log("Cannot focus Roblox window: pygetwindow is not available.")
            return False

        win = self._find_first_window(self._roblox_window_titles)
        if win is None:
            self._emit_log("Roblox window not found.")
            return False

        try:
            if getattr(win, "isMinimized", False):
                win.restore()
            win.activate()
            self._emit_log("Focused Roblox window (pygetwindow).")
            time.sleep(0.25)
            return True
        except Exception as exc:
            self._emit_log(f"Failed to focus Roblox window: {exc}")
            return False

    def _focus_google_window(self) -> bool:
        if gw is None:
            self._emit_log("Cannot focus Google window: pygetwindow is not available.")
            return False

        win = self._find_first_window(self._chrome_window_titles)
        if win is None:
            self._emit_log("Google window not found.")
            return False

        try:
            if getattr(win, "isMinimized", False):
                win.restore()
            win.activate()
            self._emit_log("Focused Google window (pygetwindow).")
            time.sleep(0.25)
            return True
        except Exception as exc:
            self._emit_log(f"Failed to focus Google window: {exc}")
            return False

    def _window_to_region(self, win):
        try:
            return (
                max(0, int(win.left)),
                max(0, int(win.top)),
                max(1, int(win.width)),
                max(1, int(win.height)),
            )
        except Exception:
            return None

    def _hold_key(self, key: str, seconds: float) -> bool:
        if pyautogui is None:
            self._emit_log(f"Cannot hold key '{key}': pyautogui is not available.")
            return False
        if self.settings.dry_run:
            self._emit_log(f"Dry-run: would hold '{key}' for {seconds:.1f}s.")
            return True

        try:
            pyautogui.keyDown(key)
        except Exception as exc:
            if self._is_failsafe_exc(exc) and self._recover_mouse_from_corner():
                try:
                    pyautogui.keyDown(key)
                except Exception as retry_exc:
                    self._emit_log(f"Failed keyDown('{key}') after fail-safe recovery: {retry_exc}")
                    return False
            else:
                self._emit_log(f"Failed keyDown('{key}'): {exc}")
                return False
        held_ok = self._sleep_interruptible(seconds)
        try:
            pyautogui.keyUp(key)
        except Exception as exc:
            if self._is_failsafe_exc(exc) and self._recover_mouse_from_corner():
                try:
                    pyautogui.keyUp(key)
                except Exception as retry_exc:
                    self._emit_log(f"Failed keyUp('{key}') after fail-safe recovery: {retry_exc}")
                    return False
            else:
                self._emit_log(f"Failed keyUp('{key}'): {exc}")
                return False
        return held_ok

    def _tap_key(self, key: str) -> bool:
        if pyautogui is None:
            self._emit_log(f"Cannot tap key '{key}': pyautogui is not available.")
            return False
        if self.settings.dry_run:
            self._emit_log(f"Dry-run: would tap '{key}'.")
            return True
        try:
            pyautogui.press(key)
            return True
        except Exception as exc:
            self._emit_log(f"Failed to tap '{key}': {exc}")
            return False

    def _align_image_to_center(
        self,
        image_name: str,
        region=None,
        tolerance_px: int = 55,
        max_steps: int = 10,
        grayscale_first: bool = True,
        confidence: float = 0.9,
    ) -> bool:
        # Shift-lock/zoomed-in alignment: only horizontal offset matters for camera yaw.
        if region:
            center_x = region[0] + (region[2] // 2)
        else:
            screen_w, _ = pyautogui.size() if pyautogui is not None else (1920, 1080)
            center_x = screen_w // 2

        for _ in range(max_steps):
            coords = self._locate_image_center(
                image_name,
                confidence=confidence,
                region=region,
                retries=1,
                retry_delay=0.0,
                emit_log=False,
                grayscale_first=grayscale_first,
            )
            if coords is None:
                return False
            target_x, _ = coords
            dx = target_x - center_x
            if abs(dx) <= tolerance_px:
                return True

            turn_key = "right" if dx > 0 else "left"
            turn_duration = min(0.12, max(0.03, abs(dx) / 1400.0))
            if not self._hold_key(turn_key, turn_duration):
                return False
            if not self._sleep_interruptible(0.04):
                return False
        return True

    def _align_target_x_to_center(
        self,
        target_x: int,
        region=None,
        tolerance_px: int = 45,
        image_name: Optional[str] = None,
        confidence: float = 0.85,
        grayscale_first: bool = False,
        max_steps: int = 10,
    ) -> bool:
        # Center mouse first, then rotate camera with arrow keys until target x aligns to center.
        if not self._move_mouse_to_center(region=region):
            if self._stop_event.is_set():
                return False
            if not self._recover_mouse_from_corner():
                return False
            if not self._move_mouse_to_center(region=region):
                return False

        if region:
            center_x = int(region[0] + (region[2] // 2))
        else:
            screen_w, _ = pyautogui.size() if pyautogui is not None else (1920, 1080)
            center_x = int(screen_w // 2)

        current_target_x = int(target_x)
        for step in range(1, max_steps + 1):
            dx = current_target_x - center_x
            if abs(dx) <= tolerance_px:
                self._emit_log(
                    "X-align complete: "
                    f"step={step}, target_x={current_target_x}, center_x={center_x}, dx={dx}."
                )
                return True

            turn_key = "right" if dx > 0 else "left"
            turn_duration = min(0.45, max(0.03, abs(dx) / 900.0))
            self._emit_log(
                "X-align step: "
                f"step={step}, target_x={current_target_x}, center_x={center_x}, "
                f"dx={dx}, turn={turn_key}, hold={turn_duration:.3f}s."
            )
            if not self._hold_key(turn_key, turn_duration):
                return False
            if not self._sleep_interruptible(0.03):
                return False

            if image_name is None:
                continue

            refreshed = self._locate_image_center(
                image_name,
                confidence=confidence,
                region=region,
                retries=1,
                retry_delay=0.0,
                emit_log=False,
                grayscale_first=grayscale_first,
            )
            if refreshed is None:
                self._emit_log(f"{image_name} lost during X-align.")
                return False
            current_target_x = int(refreshed[0])

        self._emit_log(
            "X-align reached max steps without full lock: "
            f"target_x={current_target_x}, center_x={center_x}."
        )
        return abs(current_target_x - center_x) <= tolerance_px

    def _navigate_spawn_to_ashgor(self, region=None) -> str:
        # Keep movement deterministic after spawn readiness: rotate right slightly,
        # start moving, jump once, then continue forward to reach Ashgor area.
        self._emit_log("Navigation: rotate right a bit, run+jump, then run for 3.4s.")
        if not self._hold_key("right", 0.12):
            return "stopped" if self._stop_event.is_set() else "failed"
        if not self._sleep_interruptible(0.05):
            return "stopped"
        if not self._hold_key("w", 0.25):
            return "stopped" if self._stop_event.is_set() else "failed"
        if self._stop_event.is_set():
            return "stopped"
        if not self._tap_key("space"):
            return "stopped" if self._stop_event.is_set() else "failed"
        if not self._hold_key("w", 3.6):
            return "stopped" if self._stop_event.is_set() else "failed"
        if self._stop_event.is_set():
            return "stopped"
        return "ready"

    def _resolve_screen_point(self, point) -> tuple[int, int]:
        click_x = int(point.x)
        click_y = int(point.y)
        try:
            # High-DPI display scaling can produce screenshot coordinates larger than screen size.
            screen_w, screen_h = pyautogui.size()
            if click_x >= screen_w or click_y >= screen_h:
                shot_w, shot_h = pyautogui.screenshot().size
                scale_x = shot_w / max(screen_w, 1)
                scale_y = shot_h / max(screen_h, 1)
                if scale_x > 1.1 or scale_y > 1.1:
                    click_x = int(click_x / scale_x)
                    click_y = int(click_y / scale_y)
        except Exception:
            pass
        return click_x, click_y

    def _get_roblox_region(self):
        if gw is None:
            return None
        win = self._find_first_window(self._roblox_window_titles)
        if win is None:
            return None
        return self._window_to_region(win)

    def _get_play_style_region(self):
        # Intentionally mirrors _find_and_click_play_button region logic exactly:
        # use pygetwindow bounds if available, else fall back to full screen (None).
        if gw is None:
            return None
        win = self._find_first_window(self._roblox_window_titles)
        if win is None:
            return None
        return self._window_to_region(win)

    def _locate_image_center_in_region_scaled(
        self,
        image_path: Path,
        region,
        confidence_steps: list[float],
        scale_steps: list[float],
    ):
        if pyautogui is None or Image is None:
            return None

        if region is None:
            screen_w, screen_h = pyautogui.size()
            region = (0, 0, screen_w, screen_h)

        rx, ry, rw, rh = region
        if rw <= 0 or rh <= 0:
            return None

        try:
            haystack = pyautogui.screenshot(region=region)
        except Exception:
            return None

        hay_w, hay_h = haystack.size
        scale_x = hay_w / max(rw, 1)
        scale_y = hay_h / max(rh, 1)

        try:
            with Image.open(image_path) as template_src:
                template_base = template_src.convert("RGB")
                resample = getattr(Image, "Resampling", Image).LANCZOS
                for factor in scale_steps:
                    target_w = max(1, int(template_base.width * factor))
                    target_h = max(1, int(template_base.height * factor))
                    template = template_base.resize((target_w, target_h), resample=resample)
                    for grayscale in (True, False):
                        for step_conf in confidence_steps:
                            try:
                                box = pyautogui.locate(
                                    template,
                                    haystack,
                                    grayscale=grayscale,
                                    confidence=step_conf,
                                )
                            except Exception as exc:
                                if self._is_not_found_exc(exc):
                                    box = None
                                else:
                                    try:
                                        box = pyautogui.locate(template, haystack, grayscale=grayscale)
                                    except Exception:
                                        box = None
                            if box is None:
                                continue

                            center_hay_x = box.left + (box.width / 2.0)
                            center_hay_y = box.top + (box.height / 2.0)
                            # Convert screenshot pixel coordinates back to logical screen coordinates.
                            center_region_x = int(center_hay_x / max(scale_x, 1e-9))
                            center_region_y = int(center_hay_y / max(scale_y, 1e-9))
                            return (rx + center_region_x, ry + center_region_y)
        except Exception:
            return None

        return None

    def _hover_matches_for_debug(
        self,
        image_path: Path,
        image_name: str,
        region,
        confidence_steps: list[float],
    ) -> None:
        if pyautogui is None:
            return

        self._emit_log(
            f"Debug scan for {image_name}: trying grayscale=True first, then grayscale=False."
        )
        search_region = region
        if search_region is None:
            try:
                sw, sh = pyautogui.size()
                search_region = (0, 0, sw, sh)
            except Exception:
                search_region = None

        centers: list[tuple[int, int]] = []
        for grayscale in (True, False):
            for step_conf in confidence_steps:
                try:
                    boxes = list(
                        pyautogui.locateAllOnScreen(
                            str(image_path),
                            confidence=step_conf,
                            grayscale=grayscale,
                            region=search_region,
                        )
                    )
                except Exception as exc:
                    if self._is_not_found_exc(exc):
                        boxes = []
                    else:
                        try:
                            boxes = list(
                                pyautogui.locateAllOnScreen(
                                    str(image_path), region=search_region
                                )
                            )
                        except Exception:
                            boxes = []
                if not boxes:
                    continue

                for box in boxes:
                    center_x = int(box.left + (box.width / 2.0))
                    center_y = int(box.top + (box.height / 2.0))
                    center_x, center_y = self._resolve_screen_point(
                        type("P", (), {"x": center_x, "y": center_y})()
                    )
                    centers.append((center_x, center_y))
                # Keep the first confidence tier that returns matches.
                break
            if centers:
                break

        if not centers:
            self._emit_log(f"Debug scan found 0 matches for {image_name}.")
            return

        # De-dupe near-identical centers and enforce top-to-bottom, then left-to-right.
        unique_centers: list[tuple[int, int]] = []
        for cx, cy in sorted(centers, key=lambda p: (p[1], p[0])):
            if all(abs(cx - ux) > 4 or abs(cy - uy) > 4 for ux, uy in unique_centers):
                unique_centers.append((cx, cy))

        self._emit_log(
            f"Debug scan found {len(unique_centers)} matches for {image_name}. "
            "Moving mouse to each for 2 seconds."
        )
        for idx, (cx, cy) in enumerate(unique_centers, start=1):
            if self._stop_event.is_set():
                return
            if self.settings.dry_run:
                self._emit_log(f"Dry-run: would move to match {idx} at ({cx}, {cy}).")
                continue
            try:
                pyautogui.moveTo(cx, cy)
                self._emit_log(f"Debug hover match {idx}/{len(unique_centers)} at ({cx}, {cy}).")
                if not self._sleep_interruptible(2.0):
                    return
            except Exception as exc:
                self._emit_log(f"Failed debug hover at ({cx}, {cy}): {exc}")

    def _locate_image_center(
        self,
        image_name: str,
        confidence: float = 0.9,
        region="auto",
        retries: int = 1,
        retry_delay: float = 0.25,
        emit_log: bool = True,
        grayscale_first: bool = True,
    ):
        if pyautogui is None:
            if emit_log:
                self._emit_log(f"Cannot locate {image_name}: pyautogui is not available.")
            return None

        image_path = self._resource_dir / image_name
        if not image_path.exists():
            if emit_log:
                self._emit_log(f"Template not found: {image_path}")
            return None

        if region == "auto":
            region = self._get_play_style_region()

        point = None
        if emit_log:
            self._emit_log(
                f"Looking for {image_name}..."
                + (f" (region={region})" if region else " (full screen)")
            )

        # Per user requirement: ashgor/tree templates match in grayscale, but
        # red_ashgor must be matched in color (grayscale=False).
        color_only_images = {"red_ashgor.png"}
        grayscale_only_images = {"ashgor.png", "ashgor_body.png", "tree.png"}
        if image_name in color_only_images:
            grayscale_order = (False,)
        elif image_name in grayscale_only_images:
            grayscale_order = (True,)
        else:
            grayscale_order = (True, False) if grayscale_first else (False, True)

        for attempt in range(max(1, retries)):
            last_non_not_found_exc = None
            for grayscale in grayscale_order:
                try:
                    point = pyautogui.locateCenterOnScreen(
                        str(image_path),
                        confidence=confidence,
                        grayscale=grayscale,
                        region=region,
                    )
                except Exception as exc:
                    if self._is_not_found_exc(exc):
                        point = None
                    else:
                        try:
                            point = pyautogui.locateCenterOnScreen(
                                str(image_path),
                                grayscale=grayscale,
                                region=region,
                            )
                        except Exception as fallback_exc:
                            if self._is_not_found_exc(fallback_exc):
                                point = None
                            else:
                                last_non_not_found_exc = fallback_exc
                                point = None

                if point is not None:
                    break

            if point is None and last_non_not_found_exc is not None:
                if emit_log:
                    self._emit_log(
                        "Image detection error for "
                        f"{image_name}: {last_non_not_found_exc.__class__.__name__}: "
                        f"{last_non_not_found_exc!r}"
                    )
                return None

            if point is not None:
                click_x, click_y = self._resolve_screen_point(point)
                if emit_log:
                    self._emit_log(f"Found {image_name} at ({click_x}, {click_y}).")
                return click_x, click_y

            if attempt < retries - 1 and not self._sleep_interruptible(retry_delay):
                return None

        if emit_log:
            self._emit_log(f"{image_name} not found on screen after {max(1, retries)} attempt(s).")
        return None

    def _click_image(
        self,
        image_name: str,
        confidence: float = 0.9,
        region="auto",
        retries: int = 3,
        retry_delay: float = 0.25,
    ) -> bool:
        coords = self._locate_image_center(
            image_name,
            confidence=confidence,
            region=region,
            retries=retries,
            retry_delay=retry_delay,
        )
        if coords is None:
            return False

        click_x, click_y = coords
        if self.settings.dry_run:
            self._emit_log(f"Dry-run: would click {image_name} at ({click_x}, {click_y}).")
            return True

        try:
            pyautogui.click(click_x, click_y)
            self._emit_log(f"Clicked {image_name} at ({click_x}, {click_y}).")
            return True
        except Exception as exc:
            self._emit_log(f"Failed to click {image_name}: {exc}")
            return False

    def _drag_image_to_right_edge(
        self,
        image_name: str,
        confidence: float = 0.9,
        region="auto",
        retries: int = 3,
        retry_delay: float = 0.25,
    ) -> bool:
        coords = self._locate_image_center(
            image_name,
            confidence=confidence,
            region=region,
            retries=retries,
            retry_delay=retry_delay,
        )
        if coords is None:
            return False

        start_x, start_y = coords
        try:
            screen_w, screen_h = pyautogui.size()
            target_x = max(0, screen_w - 2)
            target_y = min(max(1, start_y), max(1, screen_h - 2))
        except Exception:
            target_x = start_x + 400
            target_y = start_y

        if self.settings.dry_run:
            self._emit_log(
                f"Dry-run: would drag {image_name} from ({start_x}, {start_y}) "
                f"to ({target_x}, {target_y})."
            )
            return True

        try:
            pyautogui.moveTo(start_x, start_y, duration=0.05)
            pyautogui.mouseDown()
            pyautogui.moveTo(target_x, target_y, duration=0.45)
            pyautogui.mouseUp()
            self._emit_log(
                f"Dragged {image_name} from ({start_x}, {start_y}) to ({target_x}, {target_y})."
            )
            return True
        except Exception as exc:
            try:
                pyautogui.mouseUp()
            except Exception:
                pass
            self._emit_log(f"Failed to drag {image_name}: {exc}")
            return False

    def _click_first_available(
        self,
        image_names: list[str],
        confidence: float = 0.9,
        region="auto",
        retries: int = 3,
        retry_delay: float = 0.25,
    ) -> bool:
        for image_name in image_names:
            if self._click_image(
                image_name,
                confidence=confidence,
                region=region,
                retries=retries,
                retry_delay=retry_delay,
            ):
                return True
        return False

    def _find_ashgor_variant(self, region=None):
        sequence = ["ashgor.png", "red_ashgor.png", "ashgor.png", "red_ashgor.png"]
        for image_name in sequence:
            coords = self._locate_image_center(
                image_name,
                confidence=0.9,
                region=region,
                retries=1,
                retry_delay=0.2,
            )
            if coords is not None:
                return image_name, coords
        return None, None
    
    def _wait_for_index_or_inventory(self, timeout_seconds: float):
        candidates = ("index.png", "inventory.png", "index2.png")
        deadline = time.time() + timeout_seconds
        while time.time() < deadline and not self._stop_event.is_set():
            region = self._get_play_style_region()
            for candidate in candidates:
                coords = self._locate_image_center(
                    candidate,
                    confidence=0.9,
                    region=region,
                    retries=1,
                    retry_delay=0.0,
                )
                if coords is not None:
                    return candidate, coords

            remaining = deadline - time.time()
            if remaining > 0 and not self._sleep_interruptible(min(0.5, remaining)):
                return None, None
        return None, None

    def _wait_after_shuffle_until_ready(self) -> str:
        # Called immediately after a shuffle click.
        while not self._stop_event.is_set():
            self._emit_log("Shuffle clicked. Waiting for index/inventory (max 15 seconds)...")
            gate_image, gate_coords = self._wait_for_index_or_inventory(timeout_seconds=15.0)
            if self._stop_event.is_set():
                return "stopped"
            if gate_coords is not None:
                self._emit_log(f"{gate_image} detected after shuffle.")
                self._emit_log("Zooming in fully after shuffle (holding 'i' for 1.0s)...")
                if not self._hold_key("i", 1.0):
                    if self._stop_event.is_set():
                        return "stopped"
                    self._emit_log("Failed to zoom-in after shuffle.")
                    return "zoom_failed"
                return "ready"

            self._emit_log(
                "No index/inventory match after 15 seconds. Returning to Chrome and shuffling again..."
            )
            self._focus_google_window()
            if not self._click_image(
                "shuffle.png",
                confidence=0.9,
                region=None,
                retries=6,
                retry_delay=0.3,
            ):
                return "shuffle_click_failed"
        return "stopped"

    def _find_and_click_play_button(self) -> bool:
        if pyautogui is None:
            self._emit_log("Cannot locate play button: pyautogui is not available.")
            return False

        if self._play_button_pos is not None:
            cached_x, cached_y = self._play_button_pos
            try:
                pyautogui.click(cached_x, cached_y)
                self._emit_log(f"Clicked play button at cached position ({cached_x}, {cached_y}).")
                return True
            except Exception as exc:
                self._emit_log(f"Cached play click failed; re-locating template: {exc}")
                self._play_button_pos = None

        template_path = self._resource_dir / "play_button.png"
        if not template_path.exists():
            self._emit_log(f"Template not found: {template_path}")
            return False

        region = self._get_play_style_region()

        self._emit_log(
            f"Looking for {template_path.name}..."
            + (f" (region={region})" if region else " (full screen)")
        )
        try:
            # confidence requires OpenCV; if unavailable, fallback to default match.
            try:
                point = pyautogui.locateCenterOnScreen(
                    str(template_path), confidence=0.9, grayscale=True, region=region
                )
            except Exception as exc:
                if self._is_not_found_exc(exc):
                    point = None
                else:
                    point = pyautogui.locateCenterOnScreen(str(template_path), region=region)
        except Exception as exc:
            if self._is_not_found_exc(exc):
                point = None
            else:
                self._emit_log(
                    f"Image detection error for play button: {exc.__class__.__name__}: {exc!r}"
                )
                return False

        if point is None:
            self._emit_log("play_button.png not found on screen.")
            return False

        click_x, click_y = self._resolve_screen_point(point)

        try:
            pyautogui.click(click_x, click_y)
            self._play_button_pos = (click_x, click_y)
            self._emit_log(f"Clicked play button at ({click_x}, {click_y}).")
            return True
        except Exception as exc:
            self._emit_log(f"Failed to click play button: {exc}")
            return False

    def _run(self) -> None:
        success = False
        finish_reason = "Stopped"
        try:
            self._play_button_pos = None
            self._emit_status("Starting...", "#FFD166")
            self._emit_log("GameBot starting...")
            self._emit_log(f"Target: {self.settings.target_name}")

            if self.settings.auto_focus:
                self._emit_log("Auto-focus enabled.")
            self._emit_log("Image detection enabled.")
            if self.settings.safety_checks:
                self._emit_log("Safety checks enabled.")
            if self.settings.runtime_limit_enabled:
                self._emit_log(f"Runtime limit enabled: {self.settings.runtime_limit_minutes:.1f} minute(s).")
            if self.settings.loop_limit_enabled:
                self._emit_log(f"Loop limit enabled: {self.settings.loop_limit_count} cycle(s).")

            self._emit_log("Searching for Roblox window...")
            if not self._focus_roblox_window():
                finish_reason = "Roblox window not found/focus failed"
                self._emit_status("Error", "#FF6B6B")
                return

            self._emit_log("Roblox focused. Waiting 0.5 second before clicking Play...")
            if not self._sleep_interruptible(0.5):
                finish_reason = "Stopped by user"
                success = True
                return

            if not self._find_and_click_play_button():
                finish_reason = "Play button not found/click failed"
                self._emit_status("Error", "#FF6B6B")
                return

            self._emit_status("Loading...", "#FFD166")
            self._emit_log("Play clicked. Waiting for index/inventory image before continuing...")
            gate_image, gate_coords = self._wait_for_index_or_inventory(timeout_seconds=60.0)
            if self._stop_event.is_set():
                finish_reason = "Stopped by user"
                success = True
                return
            if gate_coords is None:
                finish_reason = "index.png/inventory.png not found after Play"
                self._emit_status("Error", "#FF6B6B")
                return
            self._emit_log(f"{gate_image} detected. Continuing...")
            self._emit_log("Zooming in fully (holding 'i' for 1.0s)...")
            if not self._hold_key("i", 1.0):
                if self._stop_event.is_set():
                    finish_reason = "Stopped by user"
                    success = True
                else:
                    finish_reason = "Failed zoom-in key hold ('i')"
                    self._emit_status("Error", "#FF6B6B")
                return

            self._emit_status("Running", "#7CFF7C")

            cycle = 1
            started_at = time.time()
            while not self._stop_event.is_set():
                if self.settings.runtime_limit_enabled:
                    elapsed = time.time() - started_at
                    if elapsed >= self.settings.runtime_limit_minutes * 60.0:
                        finish_reason = "Reached runtime limit"
                        break
                if self.settings.loop_limit_enabled and cycle > self.settings.loop_limit_count:
                    finish_reason = "Reached loop limit"
                    break

                roblox_region = self._get_play_style_region()
                nav_state = self._navigate_spawn_to_ashgor(region=roblox_region)
                if nav_state == "stopped":
                    finish_reason = "Stopped by user"
                    success = True
                    return
                if nav_state in ("red", "shuffle"):
                    if nav_state == "red":
                        self._emit_log("Matched red_ashgor.png. Focusing Google and clicking shuffle...")
                    else:
                        self._emit_log("Navigation returned 'shuffle'. Focusing Google and clicking shuffle...")
                    self._focus_google_window()
                    if not self._click_image("shuffle.png", confidence=0.9, region=None, retries=6, retry_delay=0.3):
                        finish_reason = "shuffle.png not found/click failed"
                        self._emit_status("Error", "#FF6B6B")
                        return
                    shuffle_state = self._wait_after_shuffle_until_ready()
                    if shuffle_state == "stopped":
                        finish_reason = "Stopped by user"
                        success = True
                        return
                    if shuffle_state != "ready":
                        if shuffle_state == "zoom_failed":
                            finish_reason = "Zoom-in after shuffle failed"
                        elif shuffle_state == "shuffle_click_failed":
                            finish_reason = "shuffle.png retry not found/click failed"
                        else:
                            finish_reason = f"Shuffle readiness failed: {shuffle_state}"
                        self._emit_status("Error", "#FF6B6B")
                        return
                    self._emit_log("Cycle complete (shuffle ready).")
                    cycle += 1
                    continue

                if nav_state != "ready":
                    finish_reason = "Failed spawn-to-Ashgor navigation"
                    self._emit_status("Error", "#FF6B6B")
                    return

                red_after_nav = self._locate_image_center(
                    "red_ashgor.png",
                    confidence=0.9,
                    region=roblox_region,
                    retries=3,
                    retry_delay=0.15,
                )
                if red_after_nav is not None:
                    self._emit_log(
                        "red_ashgor.png detected after navigation. "
                        "Skipping auto and shuffling."
                    )
                    self._focus_google_window()
                    if not self._click_image(
                        "shuffle.png",
                        confidence=0.9,
                        region=None,
                        retries=6,
                        retry_delay=0.3,
                    ):
                        finish_reason = "shuffle.png not found/click failed"
                        self._emit_status("Error", "#FF6B6B")
                        return
                    shuffle_state = self._wait_after_shuffle_until_ready()
                    if shuffle_state == "stopped":
                        finish_reason = "Stopped by user"
                        success = True
                        return
                    if shuffle_state != "ready":
                        if shuffle_state == "zoom_failed":
                            finish_reason = "Zoom-in after shuffle failed"
                        elif shuffle_state == "shuffle_click_failed":
                            finish_reason = "shuffle.png retry not found/click failed"
                        else:
                            finish_reason = f"Shuffle readiness failed: {shuffle_state}"
                        self._emit_status("Error", "#FF6B6B")
                        return
                    self._emit_log("Cycle complete (red detected post-navigation; shuffle ready).")
                    cycle += 1
                    continue

                self._emit_log("Navigation complete. Executing engage sequence...")
                self._emit_log("Preparing auto click (holding 'o' for 0.2s)...")
                if not self._hold_key("o", 0.2):
                    if self._stop_event.is_set():
                        finish_reason = "Stopped by user"
                        success = True
                    else:
                        finish_reason = "Failed pre-auto key hold ('o')"
                        self._emit_status("Error", "#FF6B6B")
                    return

                if not self._click_first_available(
                    ["auto_button1.png", "auto_button.png"],
                    confidence=0.9,
                    region=roblox_region,
                    retries=6,
                    retry_delay=0.3,
                ):
                    finish_reason = "auto_button1.png not found/click failed"
                    self._emit_status("Error", "#FF6B6B")
                    return
                if not self._sleep_interruptible(0.5):
                    finish_reason = "Stopped by user"
                    success = True
                    return
                if not self._drag_image_to_right_edge(
                    "radius.png", confidence=0.9, region=roblox_region, retries=4, retry_delay=0.2
                ):
                    finish_reason = "radius.png not found/drag failed"
                    self._emit_status("Error", "#FF6B6B")
                    return
                if not self._click_image(
                    "start_auto.png", confidence=0.9, region=roblox_region, retries=4, retry_delay=0.2
                ):
                    finish_reason = "start_auto.png not found/click failed"
                    self._emit_status("Error", "#FF6B6B")
                    return

                self._emit_log("Auto started. Looking for red_ashgor.png (max 4 seconds)...")
                red_found = False
                red_deadline = time.time() + 4.0
                while time.time() < red_deadline and not self._stop_event.is_set():
                    red_coords = self._locate_image_center(
                        "red_ashgor.png",
                        confidence=0.9,
                        region=roblox_region,
                        retries=1,
                        retry_delay=0.0,
                    )
                    if red_coords is not None:
                        red_found = True
                        self._emit_log("red_ashgor.png detected (boss defeated).")
                        break
                    if not self._sleep_interruptible(0.25):
                        finish_reason = "Stopped by user"
                        success = True
                        return

                if self._stop_event.is_set():
                    finish_reason = "Stopped by user"
                    success = True
                    return

                if not red_found:
                    self._emit_log(
                        "No red_ashgor.png match after 4 seconds. "
                        "Applying fallback movement: rotate left 0.2s, move forward 0.3s."
                    )
                    if not self._hold_key("left", 0.2):
                        finish_reason = "Failed fallback rotate left"
                        self._emit_status("Error", "#FF6B6B")
                        return
                    if not self._hold_key("w", 0.3):
                        finish_reason = "Stopped by user"
                        success = True
                        return

                self._emit_log("Cycle complete. Focusing Google and clicking shuffle...")
                self._focus_google_window()
                if not self._click_image(
                    "shuffle.png",
                    confidence=0.9,
                    region=None,
                    retries=6,
                    retry_delay=0.3,
                ):
                    finish_reason = "shuffle.png not found/click failed"
                    self._emit_status("Error", "#FF6B6B")
                    return
                shuffle_state = self._wait_after_shuffle_until_ready()
                if shuffle_state == "stopped":
                    finish_reason = "Stopped by user"
                    success = True
                    return
                if shuffle_state != "ready":
                    if shuffle_state == "zoom_failed":
                        finish_reason = "Zoom-in after shuffle failed"
                    elif shuffle_state == "shuffle_click_failed":
                        finish_reason = "shuffle.png retry not found/click failed"
                    else:
                        finish_reason = f"Shuffle readiness failed: {shuffle_state}"
                    self._emit_status("Error", "#FF6B6B")
                    return
                self._emit_log("Shuffle completed and ready. Cycle complete.")
                cycle += 1
                continue

            success = True
            if finish_reason == "Stopped":
                finish_reason = "Stopped by user" if self._stop_event.is_set() else "Completed"
        except Exception as exc:  # pragma: no cover
            finish_reason = f"Error: {exc}"
            self._emit_log(f"GameBot error: {exc}")
            self._emit_status("Error", "#FF6B6B")
        finally:
            with self._lock:
                self._running = False
            self._emit_finished(success, finish_reason)

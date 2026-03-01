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
    cycle_limit_enabled: bool = False
    cycle_limit_count: int = 10
    reshuffle_on_load_timeout_enabled: bool = False
    reshuffle_on_load_timeout_seconds: float = 45.0


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
        self._click_point_cache: dict[str, tuple[int, int]] = {}
        self._ashgor_body_missing_warned = False
        self._resource_dir = _resource_base_dir()
        self._roblox_window_titles = (
            "Roblox Player",
            "RobloxPlayerBeta",
            "Roblox",
            "Bloxstrap",
        )
        self._chrome_window_titles = ("Google Chrome", "Chrome", "Google")
        self._saved_pyautogui_pause: Optional[float] = None
        self._saved_pyautogui_minimum_sleep: Optional[float] = None
        self._runtime_timing_tuned = False
        self._runtime_deadline: Optional[float] = None
        self._runtime_limit_reached = False

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

    def _configure_runtime_timing(self) -> None:
        if pyautogui is None or self._runtime_timing_tuned:
            return
        try:
            self._saved_pyautogui_pause = getattr(pyautogui, "PAUSE", None)
            if self._saved_pyautogui_pause is not None:
                # Keep a small pause to avoid key/mouse burst drift over long runs.
                pyautogui.PAUSE = 0.02

            self._saved_pyautogui_minimum_sleep = getattr(pyautogui, "MINIMUM_SLEEP", None)
            if self._saved_pyautogui_minimum_sleep is not None:
                pyautogui.MINIMUM_SLEEP = 0.02

            self._runtime_timing_tuned = True
            self._emit_log("Timing mode enabled: balanced PyAutoGUI timing for stable cycle speed.")
        except Exception as exc:
            self._emit_log(f"Failed to tune runtime timing: {exc}")

    def _restore_runtime_timing(self) -> None:
        if pyautogui is None or not self._runtime_timing_tuned:
            return
        try:
            if self._saved_pyautogui_pause is not None:
                pyautogui.PAUSE = self._saved_pyautogui_pause
            if self._saved_pyautogui_minimum_sleep is not None:
                pyautogui.MINIMUM_SLEEP = self._saved_pyautogui_minimum_sleep
        except Exception:
            pass
        finally:
            self._runtime_timing_tuned = False

    def _sleep_interruptible(self, seconds: float) -> bool:
        deadline = time.perf_counter() + max(0.0, float(seconds))
        while True:
            if self._stop_event.is_set() or self._runtime_limit_hit():
                return False
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return True
            time.sleep(0.01 if remaining > 0.01 else remaining)

    def _runtime_limit_hit(self) -> bool:
        if (
            not self.settings.runtime_limit_enabled
            or self._runtime_deadline is None
        ):
            return False
        if time.perf_counter() < self._runtime_deadline:
            return False
        if not self._runtime_limit_reached:
            self._runtime_limit_reached = True
            self._stop_event.set()
            self._emit_log("Runtime limit reached. Stopping immediately.")
        return True

    def _interrupt_reason(self) -> str:
        return "Reached runtime limit" if self._runtime_limit_reached else "Stopped by user"

    def _cycle_pacing_delay(self) -> bool:
        delay = max(0.0, float(self.settings.loop_delay_seconds))
        if delay <= 0.0:
            return True
        self._emit_log(f"Cycle pacing: waiting {delay:.2f}s before next cycle.")
        return self._sleep_interruptible(delay)

    @staticmethod
    def _is_not_found_exc(exc: Exception) -> bool:
        return exc.__class__.__name__ == "ImageNotFoundException"

    @staticmethod
    def _is_failsafe_exc(exc: Exception) -> bool:
        name = exc.__class__.__name__.lower()
        msg = str(exc).lower()
        return ("failsafe" in name) or ("fail-safe" in msg) or ("failsafe" in msg)

    def _force_key_up(self, key: str) -> None:
        if pyautogui is None:
            return
        prev_failsafe = getattr(pyautogui, "FAILSAFE", None)
        try:
            if prev_failsafe is not None:
                pyautogui.FAILSAFE = False
            pyautogui.keyUp(key)
        except Exception:
            pass
        finally:
            if prev_failsafe is not None:
                pyautogui.FAILSAFE = prev_failsafe

    def _release_motion_keys(self) -> None:
        # Defensive release to prevent residual movement/camera keys between cycles.
        for motion_key in ("left", "right", "up", "down", "w", "a", "s", "d", "o"):
            self._force_key_up(motion_key)

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

    def _find_first_window(
        self,
        candidate_titles: tuple[str, ...],
        exclude_titles: tuple[str, ...] = (),
    ):
        if gw is None:
            return None
        include_tokens = [t.strip().lower() for t in candidate_titles if isinstance(t, str) and t.strip()]
        exclude_tokens = [t.strip().lower() for t in exclude_titles if isinstance(t, str) and t.strip()]
        if not include_tokens:
            return None

        def _window_score(win_obj, title_lower: str) -> int:
            try:
                width = max(0, int(getattr(win_obj, "width", 0)))
                height = max(0, int(getattr(win_obj, "height", 0)))
                area = width * height
            except Exception:
                area = 0
            try:
                minimized = bool(getattr(win_obj, "isMinimized", False))
            except Exception:
                minimized = False

            score = area
            if not minimized:
                score += 10_000_000
            if any(title_lower == token for token in include_tokens):
                score += 20_000_000
            if any(title_lower.startswith(token) for token in include_tokens):
                score += 5_000_000
            return score

        best_win = None
        best_score = -1
        try:
            all_windows = gw.getAllWindows()
        except Exception:
            all_windows = []

        for win in all_windows:
            try:
                raw_title = getattr(win, "title", "")
            except Exception:
                raw_title = ""
            title = raw_title.strip() if isinstance(raw_title, str) else ""
            if not title:
                continue
            title_lower = title.lower()
            if not any(token in title_lower for token in include_tokens):
                continue
            if any(token in title_lower for token in exclude_tokens):
                continue

            score = _window_score(win, title_lower)
            if score > best_score:
                best_score = score
                best_win = win

        if best_win is not None:
            return best_win

        # Fallback for environments where getAllWindows can be stale.
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
                if not isinstance(win_title, str) or not win_title.strip():
                    continue
                win_title_lower = win_title.strip().lower()
                if any(token in win_title_lower for token in exclude_tokens):
                    continue
                return win
        return None

    def _focus_roblox_window(self) -> bool:
        if gw is None:
            self._emit_log("Cannot focus Roblox window: pygetwindow is not available.")
            return False

        win = self._find_first_window(
            self._roblox_window_titles,
            exclude_titles=self._chrome_window_titles,
        )
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
        hold_seconds = max(0.0, float(seconds))
        if self.settings.dry_run:
            self._emit_log(f"Dry-run: would hold '{key}' for {hold_seconds:.2f}s.")
            return True

        key_down_sent = False
        release_ok = True
        try:
            self._force_key_up(key)
            pyautogui.keyDown(key)
            key_down_sent = True
        except Exception as exc:
            if self._is_failsafe_exc(exc) and self._recover_mouse_from_corner():
                try:
                    pyautogui.keyDown(key)
                    key_down_sent = True
                except Exception as retry_exc:
                    self._emit_log(f"Failed keyDown('{key}') after fail-safe recovery: {retry_exc}")
                    return False
            else:
                self._emit_log(f"Failed keyDown('{key}'): {exc}")
                return False
        held_ok = self._sleep_interruptible(hold_seconds)
        try:
            pyautogui.keyUp(key)
        except Exception as exc:
            if self._is_failsafe_exc(exc) and self._recover_mouse_from_corner():
                try:
                    pyautogui.keyUp(key)
                except Exception as retry_exc:
                    self._emit_log(f"Failed keyUp('{key}') after fail-safe recovery: {retry_exc}")
                    release_ok = False
            else:
                self._emit_log(f"Failed keyUp('{key}'): {exc}")
                release_ok = False
        finally:
            if key_down_sent:
                # Ensure no movement key remains stuck down.
                self._force_key_up(key)

        if not release_ok:
            return False
        # Give the game a tiny settle window after key release.
        if not self._sleep_interruptible(0.06):
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
        # jump once, then continue forward to reach Ashgor area.
        self._emit_log("Navigation: rotate right, settle, jump, then run to Ashgor spawn.")
        self._release_motion_keys()
        if not self._sleep_interruptible(0.5):
            return "stopped"
        if not self._hold_key("right", 0.23):
            return "stopped" if self._stop_event.is_set() else "failed"
        if not self._sleep_interruptible(0.14):
            return "stopped"
        if not self._tap_key("space"):
            return "stopped" if self._stop_event.is_set() else "failed"
        if not self._sleep_interruptible(0.12):
            return "stopped"
        if not self._hold_key("w", 4.4):
            return "stopped" if self._stop_event.is_set() else "failed"
        if not self._sleep_interruptible(0.12):
            return "stopped"
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
        win = self._find_first_window(
            self._roblox_window_titles,
            exclude_titles=self._chrome_window_titles,
        )
        if win is None:
            return None
        return self._window_to_region(win)

    def _get_google_region(self):
        if gw is None:
            return None
        win = self._find_first_window(self._chrome_window_titles)
        if win is None:
            return None
        return self._window_to_region(win)

    def _get_play_style_region(self):
        # Keep play detection scoped to Roblox window on Windows when available.
        if gw is None:
            return None
        win = self._find_first_window(
            self._roblox_window_titles,
            exclude_titles=self._chrome_window_titles,
        )
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

        # ashgor_health detection is always grayscale per user requirement.
        if image_name in {"ashgor_health.png"}:
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
        cached_coords = self._click_point_cache.get(image_name)
        if cached_coords is not None:
            click_x, click_y = cached_coords
            if self.settings.dry_run:
                self._emit_log(
                    f"Dry-run: would click {image_name} at cached position ({click_x}, {click_y})."
                )
                return True
            try:
                pyautogui.click(click_x, click_y)
                self._emit_log(f"Clicked {image_name} at cached position ({click_x}, {click_y}).")
                return True
            except Exception as exc:
                self._emit_log(f"Cached click failed for {image_name}; re-locating template: {exc}")
                self._click_point_cache.pop(image_name, None)

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
            self._click_point_cache[image_name] = (click_x, click_y)
            self._emit_log(f"Dry-run: would click {image_name} at ({click_x}, {click_y}).")
            return True

        try:
            pyautogui.click(click_x, click_y)
            self._click_point_cache[image_name] = (click_x, click_y)
            self._emit_log(f"Cached click position for {image_name} at ({click_x}, {click_y}).")
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
        cached_coords = self._click_point_cache.get(image_name)
        if cached_coords is not None:
            start_x, start_y = cached_coords
        else:
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
            self._click_point_cache[image_name] = (start_x, start_y)
            return True

        try:
            pyautogui.moveTo(start_x, start_y, duration=0.05)
            pyautogui.mouseDown()
            pyautogui.moveTo(target_x, target_y, duration=0.45)
            pyautogui.mouseUp()
            self._click_point_cache[image_name] = (start_x, start_y)
            self._emit_log(f"Cached drag start for {image_name} at ({start_x}, {start_y}).")
            self._emit_log(
                f"Dragged {image_name} from ({start_x}, {start_y}) to ({target_x}, {target_y})."
            )
            return True
        except Exception as exc:
            try:
                pyautogui.mouseUp()
            except Exception:
                pass
            self._click_point_cache.pop(image_name, None)
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

    def _wait_for_index_or_inventory(self, timeout_seconds: Optional[float]):
        candidates = ("index.png", "inventory.png", "index2.png")
        deadline = (
            None
            if timeout_seconds is None
            else (time.time() + max(0.0, float(timeout_seconds)))
        )
        while not self._stop_event.is_set():
            if deadline is not None and time.time() >= deadline:
                break
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

            sleep_for = 0.5
            if deadline is not None:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                sleep_for = min(0.5, remaining)
            if not self._sleep_interruptible(sleep_for):
                return None, None
        return None, None

    def _load_wait_timeout_seconds(self) -> Optional[float]:
        if not self.settings.reshuffle_on_load_timeout_enabled:
            return None
        return max(1.0, float(self.settings.reshuffle_on_load_timeout_seconds))

    def _wait_for_player_load_after_play(self):
        timeout_seconds = self._load_wait_timeout_seconds()
        if timeout_seconds is None:
            self._emit_log("Play clicked. Waiting indefinitely for index/inventory image...")
            gate_image, gate_coords = self._wait_for_index_or_inventory(timeout_seconds=None)
            if self._stop_event.is_set():
                return "stopped", None, None
            if gate_coords is not None:
                return "ready", gate_image, gate_coords
            return "load_wait_failed", None, None

        reshuffle_attempt = 1
        self._emit_log(
            "Play clicked. Waiting up to "
            f"{timeout_seconds:.1f}s for index/inventory before reshuffle..."
        )
        while not self._stop_event.is_set():
            gate_image, gate_coords = self._wait_for_index_or_inventory(timeout_seconds=timeout_seconds)
            if gate_coords is not None:
                return "ready", gate_image, gate_coords
            if self._stop_event.is_set():
                return "stopped", None, None

            self._emit_log(
                "No index/inventory detected after "
                f"{timeout_seconds:.1f}s. Reshuffling server (attempt {reshuffle_attempt})..."
            )
            reshuffle_attempt += 1
            self._focus_google_window()
            if not self._click_image(
                "shuffle.png",
                confidence=0.9,
                region=None,
                retries=6,
                retry_delay=0.3,
            ):
                self._emit_log("shuffle.png not found/click failed during load-time reshuffle.")
                return "shuffle_click_failed", None, None
            if not self._focus_roblox_window():
                self._emit_log(
                    "Failed to refocus Roblox after load-time reshuffle click. Continuing anyway."
                )
            if not self._sleep_interruptible(0.5):
                return "stopped", None, None
            self._emit_log("Reshuffle clicked. Looking for index/inventory again...")

        return "stopped", None, None

    def _wait_after_shuffle_until_ready(self) -> str:
        # Called immediately after a shuffle click.
        timeout_seconds = self._load_wait_timeout_seconds()
        if timeout_seconds is None:
            self._emit_log("Shuffle clicked. Waiting indefinitely for index/inventory...")
            gate_image, gate_coords = self._wait_for_index_or_inventory(timeout_seconds=None)
            if self._stop_event.is_set() or gate_coords is None:
                return "stopped"
            self._emit_log(f"{gate_image} detected after shuffle.")
            if not self._focus_roblox_window():
                self._emit_log("Failed to focus Roblox before post-shuffle zoom-out.")
                return "focus_failed"
            self._emit_log("Attempting post-shuffle zoom-out (holding 'o' for 1.0s)...")
            if not self._hold_key("o", 1.0):
                if self._stop_event.is_set():
                    return "stopped"
                self._emit_log("Failed to zoom-out after shuffle.")
                return "zoom_failed"
            self._emit_log("Post-shuffle zoom-out key sequence sent.")
            return "ready"

        while not self._stop_event.is_set():
            self._emit_log(
                "Shuffle clicked. Waiting up to "
                f"{timeout_seconds:.1f}s for index/inventory..."
            )
            gate_image, gate_coords = self._wait_for_index_or_inventory(
                timeout_seconds=timeout_seconds
            )
            if self._stop_event.is_set():
                return "stopped"
            if gate_coords is not None:
                self._emit_log(f"{gate_image} detected after shuffle.")
                if not self._focus_roblox_window():
                    self._emit_log("Failed to focus Roblox before post-shuffle zoom-out.")
                    return "focus_failed"
                self._emit_log("Attempting post-shuffle zoom-out (holding 'o' for 1.0s)...")
                if not self._hold_key("o", 1.0):
                    if self._stop_event.is_set():
                        return "stopped"
                    self._emit_log("Failed to zoom-out after shuffle.")
                    return "zoom_failed"
                self._emit_log("Post-shuffle zoom-out key sequence sent.")
                return "ready"

            self._emit_log(
                "No index/inventory match after "
                f"{timeout_seconds:.1f}s. Returning to Chrome and shuffling again..."
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

        cached_play = self._click_point_cache.get("play_button.png")
        if cached_play is not None:
            cached_x, cached_y = cached_play
            try:
                pyautogui.click(cached_x, cached_y)
                self._emit_log(f"Clicked play button at cached position ({cached_x}, {cached_y}).")
                return True
            except Exception as exc:
                self._emit_log(f"Cached play click failed; re-locating template: {exc}")
                self._click_point_cache.pop("play_button.png", None)

        template_path = self._resource_dir / "play_button.png"
        if not template_path.exists():
            self._emit_log(f"Template not found: {template_path}")
            return False

        search_targets: list[tuple[str, Optional[tuple[int, int, int, int]]]] = []
        roblox_region = self._get_roblox_region()
        if roblox_region is not None:
            search_targets.append(("Roblox window", roblox_region))
        google_region = self._get_google_region()
        if google_region is not None and google_region != roblox_region:
            search_targets.append(("Google Chrome window", google_region))
        search_targets.append(("full screen", None))

        coords = None
        for label, region in search_targets:
            self._emit_log(
                f"Looking for play_button.png in {label}..."
                + (f" (region={region})" if region else "")
            )
            coords = self._locate_image_center(
                "play_button.png",
                confidence=0.9,
                region=region,
                retries=2,
                retry_delay=0.25,
                emit_log=False,
                grayscale_first=True,
            )
            if coords is not None:
                break

        if coords is None:
            self._emit_log("play_button.png not found on screen.")
            return False

        click_x, click_y = coords

        try:
            pyautogui.click(click_x, click_y)
            self._click_point_cache["play_button.png"] = (click_x, click_y)
            self._emit_log(f"Cached click position for play_button.png at ({click_x}, {click_y}).")
            self._emit_log(f"Clicked play button at ({click_x}, {click_y}).")
            return True
        except Exception as exc:
            self._emit_log(f"Failed to click play button: {exc}")
            return False

    def _run(self) -> None:
        success = False
        finish_reason = "Stopped"
        try:
            self._click_point_cache.clear()
            self._configure_runtime_timing()
            self._runtime_limit_reached = False
            self._runtime_deadline = (
                time.perf_counter() + (max(0.0, float(self.settings.runtime_limit_minutes)) * 60.0)
                if self.settings.runtime_limit_enabled
                else None
            )

            cycle_limit_enabled = bool(
                self.settings.cycle_limit_enabled or self.settings.loop_limit_enabled
            )
            cycle_limit_count = int(
                self.settings.cycle_limit_count
                if self.settings.cycle_limit_enabled
                else self.settings.loop_limit_count
            )
            cycle_limit_count = max(1, cycle_limit_count)

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
            if cycle_limit_enabled:
                self._emit_log(f"Cycle limit enabled: {cycle_limit_count} cycle(s).")
            if self.settings.reshuffle_on_load_timeout_enabled:
                self._emit_log(
                    "Load timeout reshuffle enabled: "
                    f"{self.settings.reshuffle_on_load_timeout_seconds:.1f}s."
                )
            else:
                self._emit_log("Load timeout reshuffle disabled: waiting indefinitely for player load.")

            self._emit_log("Preparing focus before Play click...")
            roblox_focused = self._focus_roblox_window()
            if roblox_focused:
                self._emit_log("Roblox focused.")
            else:
                self._emit_log(
                    "Roblox window not found yet. Trying Google Chrome focus for web Play flow..."
                )
                if self._focus_google_window():
                    self._emit_log("Google Chrome focused.")
                else:
                    self._emit_log(
                        "Could not focus Roblox/Chrome. Continuing with full-screen Play scan."
                    )

            self._emit_log("Waiting 0.5 second before clicking Play...")
            if not self._sleep_interruptible(0.5):
                finish_reason = self._interrupt_reason()
                success = True
                return

            if not self._find_and_click_play_button():
                self._emit_log(
                    "Play button not found on first attempt. Refocusing and retrying once..."
                )
                if not self._focus_google_window():
                    self._focus_roblox_window()
                if not self._sleep_interruptible(0.35):
                    finish_reason = self._interrupt_reason()
                    success = True
                    return
                if not self._find_and_click_play_button():
                    finish_reason = "Play button not found/click failed"
                    self._emit_status("Error", "#FF6B6B")
                    return

            self._emit_status("Loading...", "#FFD166")
            load_state, gate_image, gate_coords = self._wait_for_player_load_after_play()
            if load_state == "stopped" or self._stop_event.is_set():
                finish_reason = self._interrupt_reason()
                success = True
                return
            if load_state != "ready" or gate_coords is None:
                if load_state == "shuffle_click_failed":
                    finish_reason = "shuffle.png not found/click failed during load-time reshuffle"
                else:
                    finish_reason = "Failed to detect index/inventory after Play"
                self._emit_status("Error", "#FF6B6B")
                return
            self._emit_log(f"{gate_image} detected. Continuing...")
            if not self._focus_roblox_window():
                finish_reason = "Roblox focus failed before initial zoom-out"
                self._emit_status("Error", "#FF6B6B")
                return
            self._emit_log("Attempting zoom-out (holding 'o' for 1.0s)...")
            if not self._hold_key("o", 1.0):
                if self._stop_event.is_set():
                    finish_reason = self._interrupt_reason()
                    success = True
                else:
                    finish_reason = "Failed zoom-out key hold ('o')"
                    self._emit_status("Error", "#FF6B6B")
                return
            self._emit_log("Zoom-out key sequence sent.")
            self._release_motion_keys()
            if not self._sleep_interruptible(0.15):
                finish_reason = self._interrupt_reason()
                success = True
                return

            self._emit_status("Running", "#7CFF7C")

            completed_cycles = 0
            while not self._stop_event.is_set():
                if self._runtime_limit_hit():
                    finish_reason = "Reached runtime limit"
                    break
                if cycle_limit_enabled and completed_cycles >= cycle_limit_count:
                    finish_reason = "Reached cycle limit"
                    break

                roblox_region = self._get_play_style_region()
                if not self._focus_roblox_window():
                    finish_reason = "Roblox focus failed before navigation"
                    self._emit_status("Error", "#FF6B6B")
                    return
                nav_state = self._navigate_spawn_to_ashgor(region=roblox_region)
                if nav_state == "stopped":
                    finish_reason = self._interrupt_reason()
                    success = True
                    return
                if nav_state == "shuffle":
                    self._emit_log("Navigation returned 'shuffle'. Focusing Google and clicking shuffle...")
                    self._focus_google_window()
                    if not self._click_image("shuffle.png", confidence=0.9, region=None, retries=6, retry_delay=0.3):
                        finish_reason = "shuffle.png not found/click failed"
                        self._emit_status("Error", "#FF6B6B")
                        return
                    shuffle_state = self._wait_after_shuffle_until_ready()
                    if shuffle_state == "stopped":
                        finish_reason = self._interrupt_reason()
                        success = True
                        return
                    if shuffle_state != "ready":
                        if shuffle_state == "zoom_failed":
                            finish_reason = "Zoom-out after shuffle failed"
                        elif shuffle_state == "focus_failed":
                            finish_reason = "Roblox focus failed before post-shuffle zoom-out"
                        elif shuffle_state == "shuffle_click_failed":
                            finish_reason = "shuffle.png retry not found/click failed"
                        else:
                            finish_reason = f"Shuffle readiness failed: {shuffle_state}"
                        self._emit_status("Error", "#FF6B6B")
                        return
                    self._emit_log("Cycle complete (shuffle ready).")
                    completed_cycles += 1
                    if cycle_limit_enabled and completed_cycles >= cycle_limit_count:
                        finish_reason = "Reached cycle limit"
                        break
                    if not self._cycle_pacing_delay():
                        finish_reason = self._interrupt_reason()
                        success = True
                        return
                    continue

                if nav_state != "ready":
                    finish_reason = "Failed spawn-to-Ashgor navigation"
                    self._emit_status("Error", "#FF6B6B")
                    return

                if not self._focus_roblox_window():
                    finish_reason = "Roblox focus failed before engage sequence"
                    self._emit_status("Error", "#FF6B6B")
                    return
                if not self._sleep_interruptible(0.12):
                    finish_reason = self._interrupt_reason()
                    success = True
                    return
                health_after_nav = self._locate_image_center(
                    "ashgor_health.png",
                    confidence=0.92,
                    region=roblox_region,
                    retries=3,
                    retry_delay=0.15,
                    grayscale_first=True,
                )
                if health_after_nav is None:
                    self._emit_log(
                        "ashgor_health.png not detected after navigation (Ashgor not alive). "
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
                        finish_reason = self._interrupt_reason()
                        success = True
                        return
                    if shuffle_state != "ready":
                        if shuffle_state == "zoom_failed":
                            finish_reason = "Zoom-out after shuffle failed"
                        elif shuffle_state == "focus_failed":
                            finish_reason = "Roblox focus failed before post-shuffle zoom-out"
                        elif shuffle_state == "shuffle_click_failed":
                            finish_reason = "shuffle.png retry not found/click failed"
                        else:
                            finish_reason = f"Shuffle readiness failed: {shuffle_state}"
                        self._emit_status("Error", "#FF6B6B")
                        return
                    self._emit_log("Cycle complete (not alive after navigation; shuffle ready).")
                    completed_cycles += 1
                    if cycle_limit_enabled and completed_cycles >= cycle_limit_count:
                        finish_reason = "Reached cycle limit"
                        break
                    if not self._cycle_pacing_delay():
                        finish_reason = self._interrupt_reason()
                        success = True
                        return
                    continue

                self._emit_log(
                    "ashgor_health.png detected after navigation (Ashgor alive). "
                    "Executing engage sequence..."
                )
                self._emit_log("Preparing auto click (holding 'o' for 0.2s)...")
                if not self._hold_key("o", 0.2):
                    if self._stop_event.is_set():
                        finish_reason = self._interrupt_reason()
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
                    finish_reason = self._interrupt_reason()
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

                self._emit_log(
                    "Auto started. Waiting up to 25 seconds for ashgor_health.png to disappear..."
                )
                health_disappeared = False
                health_deadline = time.time() + 25.0
                while time.time() < health_deadline and not self._stop_event.is_set():
                    health_coords = self._locate_image_center(
                        "ashgor_health.png",
                        confidence=0.9,
                        region=roblox_region,
                        retries=1,
                        retry_delay=0.0,
                        grayscale_first=True,
                    )
                    if health_coords is None:
                        health_disappeared = True
                        self._emit_log("ashgor_health.png disappeared (boss defeated).")
                        break
                    if not self._sleep_interruptible(0.25):
                        finish_reason = self._interrupt_reason()
                        success = True
                        return

                if self._stop_event.is_set():
                    finish_reason = self._interrupt_reason()
                    success = True
                    return

                if not health_disappeared:
                    self._emit_log(
                        "ashgor_health.png still visible after 25 seconds. Reshuffling server."
                    )

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
                    finish_reason = self._interrupt_reason()
                    success = True
                    return
                if shuffle_state != "ready":
                    if shuffle_state == "zoom_failed":
                        finish_reason = "Zoom-out after shuffle failed"
                    elif shuffle_state == "focus_failed":
                        finish_reason = "Roblox focus failed before post-shuffle zoom-out"
                    elif shuffle_state == "shuffle_click_failed":
                        finish_reason = "shuffle.png retry not found/click failed"
                    else:
                        finish_reason = f"Shuffle readiness failed: {shuffle_state}"
                    self._emit_status("Error", "#FF6B6B")
                    return
                self._emit_log("Shuffle completed and ready. Cycle complete.")
                completed_cycles += 1
                if cycle_limit_enabled and completed_cycles >= cycle_limit_count:
                    finish_reason = "Reached cycle limit"
                    break
                if not self._cycle_pacing_delay():
                    finish_reason = self._interrupt_reason()
                    success = True
                    return
                continue

            success = True
            if finish_reason == "Stopped":
                finish_reason = self._interrupt_reason() if self._stop_event.is_set() else "Completed"
        except Exception as exc:  # pragma: no cover
            finish_reason = f"Error: {exc}"
            self._emit_log(f"GameBot error: {exc}")
            self._emit_status("Error", "#FF6B6B")
        finally:
            self._runtime_deadline = None
            self._restore_runtime_timing()
            with self._lock:
                self._running = False
            self._emit_finished(success, finish_reason)

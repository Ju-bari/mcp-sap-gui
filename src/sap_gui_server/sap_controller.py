import psutil
import subprocess
import time
import logging
import os
import winreg
import ctypes
from typing import Dict, Any, Optional
import pyautogui
import base64
from io import BytesIO
from dotenv import load_dotenv
import win32gui
import win32con
import win32com.client
import win32process
from mss import mss
from PIL import Image

logger = logging.getLogger(__name__)

_popup_hwnd = None
_main_window_hwnd = None

def _get_dpi_scale() -> float:
    """Get the DPI scaling factor for the primary monitor"""
    try:
        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()
        dpi = user32.GetDpiForSystem()
        scale = dpi / 96.0  # 96 is the base DPI
        logger.debug(f"DPI scale factor: {scale}")
        return scale
    except Exception as e:
        logger.warning(f"Failed to get DPI scale: {str(e)}")
        return 1.0  # Default to no scaling

def _handle_multiple_logon_popup(process_pid: int) -> Optional[int]:
    """Find the multiple logon popup window handle for specific SAP GUI process"""
    result = None
    
    def get_window_info(hwnd: int) -> str:
        """Get detailed window information for debugging"""
        try:
            visible = win32gui.IsWindowVisible(hwnd)
            title = win32gui.GetWindowText(hwnd)
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
            parent = win32gui.GetParent(hwnd)
            return (f"Window handle: {hwnd}\n"
                   f"  Title: '{title}'\n"
                   f"  Process ID: {pid}\n"
                   f"  Visible: {visible}\n"
                   f"  Style: 0x{style:08x}\n"
                   f"  Parent: {parent}")
        except Exception as e:
            return f"Error getting window info for {hwnd}: {str(e)}"
    
    def enum_callback(hwnd, _):
        nonlocal result
        try:
            # Get window info
            title = win32gui.GetWindowText(hwnd)
            visible = win32gui.IsWindowVisible(hwnd)
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            
            # Log all windows for debugging
            # logger.debug(f"Found window - Title: '{title}', Handle: {hwnd}, PID: {pid}, Visible: {visible}")
            
            # Skip invisible windows
            if not visible:
                # logger.debug(f"Skipping invisible window: '{title}' (hwnd: {hwnd})")
                return True
            
            # Skip windows from other processes
            if pid != process_pid:
                # logger.debug(f"Skipping window from different process: '{title}' (pid: {pid} != {process_pid})")
                return True
            
            # Log detailed info for our process's windows
            logger.debug(f"Found window from our process - Title: '{title}', Handle: {hwnd}")
            
            # Check for our target window
            if "License Information for Multiple Logons" in title:
                logger.debug(f"Found matching popup window - Title: '{title}', Handle: {hwnd}, PID: {pid}")
                logger.debug("Window details:")
                logger.debug(get_window_info(hwnd))
                result = hwnd
            
            # Always return True to continue enumeration
            return True
                
        except Exception as e:
            logger.debug(f"Error checking window {hwnd}: {str(e)}")
            # Return True to continue enumeration even if there's an error
            return True
    
    try:
        logger.debug(f"Searching for popup window for process {process_pid}")
        win32gui.EnumWindows(enum_callback, None)
        if result is None:
            logger.debug("No matching popup window found")
        return result
    except Exception as e:
        logger.error(f"Error during window enumeration: {str(e)}")
        return None

def _find_any_sap_window() -> Optional[Any]:
    """Find any SAP GUI window from saplogon.exe process"""

    result = []

    def enum_windows_callback(hwnd, _):
        try:
            # Get window info first
            title = win32gui.GetWindowText(hwnd)
            visible = win32gui.IsWindowVisible(hwnd)
            _, pid = win32process.GetWindowThreadProcessId(hwnd)

            # Log all windows for debugging
            logger.debug(f"Found window - Title: '{title}', Handle: {hwnd}, PID: {pid}, Visible: {visible}")

            # Skip invisible windows
            if not visible:
                logger.debug(f"Skipping invisible window: '{title}' (hwnd: {hwnd})")
                return True

            try:
                process = psutil.Process(pid)
                process_name = process.name().lower()

                # Log process info
                logger.debug(f"Process info - Name: {process_name}, PID: {pid}")

                # Check if window belongs to saplogon.exe
                if process_name == 'saplogon.exe':
                    # Exclude the SAP Logon window itself
                    if "SAP Logon" not in title:
                        logger.debug(f"Found SAP window - Title: '{title}', Handle: {hwnd}, PID: {pid}")
                        result.append(hwnd)
                    else:
                        logger.debug(f"Skipping SAP Logon window: '{title}' (hwnd: {hwnd})")
                else:
                    logger.debug(f"Skipping non-saplogon.exe window: '{title}' (process: {process_name})")

            except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                logger.debug(f"Process access error for window '{title}' (pid: {pid}): {str(e)}")

            # Always continue enumeration
            return True

        except Exception as e:
            logger.debug(f"Error checking window {hwnd}: {str(e)}")
            # Continue enumeration even if there's an error
            return True

    try:
        logger.debug("Searching for SAP GUI windows")
        win32gui.EnumWindows(enum_windows_callback, None)

        if not result:
            logger.debug("No SAP GUI windows found")
            return None

        # If multiple windows found, prefer the active one
        active_window = win32gui.GetForegroundWindow()
        for hwnd in result:
            if hwnd == active_window:
                logger.debug("Found active SAP GUI window")
                return hwnd

        logger.debug("No active SAP GUI window found, using first window")
        return result[0]  # Return first window if no active one found

    except Exception as e:
        logger.error(f"Error during window enumeration: {str(e)}")
        return None


def _find_sap_window_integrated(process_pid: int) -> bool:
    """Find the SAP GUI main window, handling the multi-logon popup if needed."""

    found_window = False  # Flag to track if we found the window

    def enum_windows_callback(hwnd, _):
        nonlocal found_window
        try:
            if not win32gui.IsWindow(hwnd):
                return True

            try:
                title = win32gui.GetWindowText(hwnd)
            except Exception as e:
                logger.error(f"Error getting window title for {hwnd}: {e}")
                return True

            try:
                visible = win32gui.IsWindowVisible(hwnd)
            except Exception as e:
                logger.error(f"Error checking if window {hwnd} is visible: {e}")
                return True

            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
            except Exception as e:
                logger.error(f"Error getting process ID for window {hwnd}: {e}")
                return True

            # Skip invisible windows
            if not visible:
                return True

            # Skip windows from other processes
            if pid != process_pid:
                return True

            if not title:
                return True

            logger.debug(f"Checking window: {title} ({hwnd})")

            # Check for multi-logon popup first
            if "License Information for Multiple Logons" in title:
                logger.info("Found multi-logon popup")
                global _popup_hwnd
                _popup_hwnd = hwnd
                _handle_multiple_logon_popup()  # Handle the popup immediately
                return True  # Continue searching for the main window

            # If not the popup, check for the main window
            if "License Information for Multiple Logons" not in title:
                if "SAP Logon" in title:  # Skip the logon window
                    logger.debug(f"Skipping SAP Logon window: {title}")
                    return True
                logger.info(f"Found main SAP GUI window: {title}")
                global _main_window_hwnd
                _main_window_hwnd = hwnd
                found_window = True  # Set flag instead of returning False
                logger.debug("Found main window, will continue enumeration")

            return True  # Always continue enumeration

        except Exception as e:
            logger.error(f"Error in outer try block of enum_windows_callback: {str(e)}")
            return True

    start_time = time.time()
    while time.time() - start_time < 5:  # 5-second timeout
        logger.debug(f"Searching for SAP GUI windows for process {process_pid}")
        found_window = False  # Reset flag before each enumeration
        win32gui.EnumWindows(enum_windows_callback, None)
        if found_window:  # Check if we found the window during enumeration
            logger.debug("Main window found, returning True")
            time.sleep(1.0)
            return True
        time.sleep(0.1)  # Short sleep between attempts
    logger.warning("Window search timed out after 5 seconds")
    return False

# Load environment variables from credentials.env
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'credentials.env')
if not load_dotenv(env_path):
    logger.warning(f"No credentials.env file found at {env_path}")

class SapController:
    def __init__(self):
        self._initialized = False
        self._dpi_scale = _get_dpi_scale()
        self._current_process = None
        self._main_window_hwnd = None  # Initialize the main window handle
        self._current_process = None
        # self._main_window_hwnd = None  # Initialize the main window handle, now global
        # self._popup_hwnd = None # now global
        logger.debug("SapController initialized")

    def _ensure_sap_window_active(self) -> None:
        """Ensure SAP GUI window is active, restored, and maximized"""
        logger.debug("Ensuring SAP GUI window is active and maximized")

        try:
            # Use the stored main window handle if it exists and is valid
            global _main_window_hwnd
            if _main_window_hwnd and win32gui.IsWindow(_main_window_hwnd):
                hwnd = _main_window_hwnd
                logger.debug("Using stored main window handle")
            else:
                # Fallback to finding any SAP window
                hwnd = _find_any_sap_window()
                if not hwnd:
                    raise Exception("No SAP GUI window found")
                logger.debug("Using fallback _find_any_sap_window")

            # Get window title for logging
            title = win32gui.GetWindowText(hwnd)
            logger.debug(f"Found SAP window: {title}")

            # Get window placement info
            placement = win32gui.GetWindowPlacement(hwnd)

            # Restore if minimized
            if placement[1] == win32con.SW_SHOWMINIMIZED:
                logger.debug("Window is minimized, restoring...")
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                time.sleep(0.5)

            # Maximize window
            logger.debug("Maximizing window...")
            win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
            time.sleep(0.5)

            # Force window to foreground
            logger.debug("Bringing window to foreground...")
            shell = win32com.client.Dispatch("WScript.Shell")
            shell.SendKeys('%')  # Alt key to allow SetForegroundWindow
            win32gui.SetForegroundWindow(hwnd)

            # Verify window is active with timeout
            start_time = time.time()
            timeout = 2  # Maximum wait time in seconds

            while time.time() - start_time < timeout:
                if win32gui.GetForegroundWindow() == hwnd:
                    logger.debug("Window successfully activated")
                    break
                logger.debug("Window not active yet, retrying activation...")
                shell.SendKeys('%')  # Alt key to allow SetForegroundWindow
                win32gui.SetForegroundWindow(hwnd)
                time.sleep(0.1)  # Short sleep between attempts

            if win32gui.GetForegroundWindow() != hwnd:
                logger.warning("Failed to activate window after timeout")
            else:
                logger.debug("SAP GUI window is now active and maximized")

        except Exception as e:
            logger.error(f"Failed to ensure SAP window is active: {str(e)}")
            raise Exception(f"Failed to activate SAP window: {str(e)}")
    
    def _handle_multiple_logon_popup(self) -> None:
        """Handle the multiple logon popup by selecting the second option"""
        logger.info("Checking for multiple logon popup...")

        # This function is now only called from within _find_sap_window_integrated,
        # so we can assume that _popup_hwnd is already set.

        try:
            global _popup_hwnd
            if not _popup_hwnd:
                logger.error("No popup window handle available")
                return  # Should not happen, but handle for safety

            hwnd = _popup_hwnd

            # Log window info
            title = win32gui.GetWindowText(hwnd)
            placement = win32gui.GetWindowPlacement(hwnd)
            logger.debug(f"Found multilogon popup window: {title}")
            logger.debug(f"Window placement: {placement}")

            # Activate the popup window with timeout
            logger.debug("Activating multilogon popup window")
            shell = win32com.client.Dispatch("WScript.Shell")

            start_time = time.time()
            timeout = 2  # Maximum wait time in seconds

            while time.time() - start_time < timeout:
                shell.SendKeys('%')  # Alt key to allow SetForegroundWindow
                win32gui.SetForegroundWindow(hwnd)

                if win32gui.GetForegroundWindow() == hwnd:
                    logger.debug("multilogon Popup window successfully activated")
                    break

                logger.debug("multilogon Popup window not active yet, retrying activation...")
                time.sleep(0.1)  # Short sleep between attempts

            if win32gui.GetForegroundWindow() != hwnd:
                logger.warning("Failed to activate multilogon popup window after timeout")

            # Give a short moment for the window to stabilize
            time.sleep(0.2)

            # Get window dimensions and calculate click position
            rect = win32gui.GetWindowRect(hwnd)
            width = rect[2] - rect[0]
            height = rect[3] - rect[1]
            click_x = width // 2  # Center horizontally
            click_y = int(height * 0.38)  # 38% from top
            logger.debug(f"Clicking at position ({click_x}, {click_y}) in multilogon  window of size {width}x{height}")

            # Click at calculated position
            self._click_with_dpi_scaling(hwnd, click_x, click_y, check_bounds=False)

            # Press Enter to confirm
            shell = win32com.client.Dispatch("WScript.Shell")
            shell.SendKeys('~')  # Enter key
            time.sleep(1)  # further reduced

            logger.info("Multiple logon popup handled successfully")

        except Exception as e:
            logger.error(f"Error handling multiple logon popup: {str(e)}")
            raise Exception(f"Failed to handle multiple logon popup: {str(e)}")

    def _get_sapgui_path(self) -> str:
        """Get SAP GUI installation path from registry or default location"""
        try:
            # Try to get path from registry
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\SAP\SAPGUIFrontend") as key:
                install_path = winreg.QueryValueEx(key, "InstallationPath")[0]
                logger.debug(f"Found SAP GUI path in registry: {install_path}")
                return install_path
        except WindowsError as e:
            logger.warning(f"Could not read SAP GUI path from registry: {str(e)}")
            # Fall back to default path
            default_path = r"C:\Program Files\SAP\FrontEnd\SAPGUI"
            logger.debug(f"Using default SAP GUI path: {default_path}")
            return default_path

    def launch_transaction(self, transaction: str) -> Dict[str, Any]:
        """Launch a SAP transaction"""
        logger.info(f"Launching transaction: {transaction}")
        
        try:
            # Get SAP GUI path and construct full path to sapshcut
            sapgui_path = self._get_sapgui_path()
            sapshcut_path = os.path.join(sapgui_path, "sapshcut.exe")
            
            if not os.path.exists(sapshcut_path):
                error_msg = f"sapshcut.exe not found at: {sapshcut_path}"
                logger.error(error_msg)
                raise Exception(f"Failed to launch SAP: {error_msg}")
                
            # Get credentials from environment variables
            system = os.getenv('SAP_SYSTEM')
            client = os.getenv('SAP_CLIENT')
            user = os.getenv('SAP_USER')
            password = os.getenv('SAP_PASSWORD')
            
            # Validate required environment variables
            if not all([system, client, user, password]):
                raise Exception("Missing required SAP credentials in environment variables")
            
            cmd = [
                sapshcut_path,
                "-maxgui",
                f"-system={system}",
                f"-client={client}",
                f"-command={transaction}",
                f"-user={user}",
                f"-pw={password}"
            ]
            
            logger.info("Launching SAP GUI with sapshcut command")
            logger.debug(f"Using sapshcut path: {sapshcut_path}")
            logger.debug(f"Command: {' '.join(cmd)}")
            
            # Kill any existing SAP GUI processes
            logger.info("Killing any existing SAP GUI processes")
            subprocess.run(['taskkill', '/F', '/IM', 'saplogon.exe'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(['taskkill', '/F', '/IM', 'sapshcut.exe'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1)  # Give time for processes to clean up
            
            # Launch SAP GUI
            subprocess.Popen(cmd)
            logger.debug("SAP GUI launch command executed")
            
            # Wait dynamically for saplogon.exe process to start
            start_time = time.time()
            timeout = 5  # Maximum wait time in seconds
            saplogon_process = None
            
            while time.time() - start_time < timeout:
                import psutil
                saplogon_processes = [p for p in psutil.process_iter(['pid', 'name']) 
                                    if p.info['name'].lower() == 'saplogon.exe']
                if saplogon_processes:
                    saplogon_process = saplogon_processes[0]
                    logger.debug(f"Found saplogon.exe process after {time.time() - start_time:.1f} seconds")
                    break
                time.sleep(0.5)  # Check every 500ms
                
            if not saplogon_process:
                raise Exception(f"Failed to find saplogon.exe process after {timeout} seconds")
                
            self._current_process = saplogon_process
            logger.debug(f"Found saplogon.exe process with PID: {self._current_process.pid}")
            
            # Give SAP time to open the window
            time.sleep(2)
            
            # Find the main SAP window (and handle popup if necessary)
            if _find_sap_window_integrated(self._current_process.pid):
                logger.debug(f"taking screenshot!")
                # add small delay
                time.sleep(0.5)
                # Take screenshot
                screenshot = self._take_screenshot()
                return {
                    "image": screenshot
                }
            else:
                raise Exception("Could not find main SAP window after launching transaction.")

        except Exception as e:
            logger.error(f"Failed to launch transaction {transaction}: {str(e)}")
            raise

    def _click_with_dpi_scaling(self, window_handle: int, x: int, y: int, check_bounds: bool = True) -> None:
        """Helper method to click at DPI-scaled coordinates relative to a window"""
        try:
            # Get window position and dimensions
            rect = win32gui.GetWindowRect(window_handle)
            width = rect[2] - rect[0]
            height = rect[3] - rect[1]
            
            # Validate coordinates if needed
            if check_bounds:
                if not (0 <= x <= width and 0 <= y <= height):
                    raise ValueError(f"Coordinates ({x}, {y}) are outside window bounds ({width}x{height})")
            
            # Apply DPI scaling and convert to screen coordinates
            screen_x = rect[0] + (x * self._dpi_scale)
            screen_y = rect[1] + (y * self._dpi_scale)
            logger.debug(f"Converting coordinates: window({x}, {y}) -> screen({screen_x}, {screen_y}) with DPI scale {self._dpi_scale}")
            
            # Move mouse and click
            pyautogui.moveTo(screen_x, screen_y)
            time.sleep(0.2)
            pyautogui.click()
            time.sleep(0.5)
            
        except Exception as e:
            logger.error(f"Failed to click at position ({x}, {y}): {str(e)}")
            raise Exception(f"Failed to click at position: {str(e)}")

    def click_position(self, x: int, y: int) -> Dict[str, Any]:
        """Click at specific coordinates"""
        logger.debug(f"Clicking at position ({x}, {y})")
        
        try:
            # Ensure SAP window is active before clicking
            self._ensure_sap_window_active()
            
            # Get active window handle
            active_window = win32gui.GetForegroundWindow()
            if not active_window:
                raise Exception("No active window found")
            
            # Perform click with DPI scaling, using the stored main window handle
            global _main_window_hwnd
            self._click_with_dpi_scaling(_main_window_hwnd, int(x), int(y))

            response = {
                "image": self._take_screenshot()
            }
            logger.debug("Click operation successful")
            return response

        except Exception as e:
            logger.error(f"Failed to click at position ({x}, {y}): {str(e)}")
            raise Exception(f"Failed to click at position: {str(e)}")

    def _move_with_dpi_scaling(self, window_handle: int, x: int, y: int, check_bounds: bool = True) -> None:
        """Helper method to move mouse to DPI-scaled coordinates relative to a window"""
        try:
            # Get window position and dimensions
            rect = win32gui.GetWindowRect(window_handle)
            width = rect[2] - rect[0]
            height = rect[3] - rect[1]

            # Validate coordinates if needed
            if check_bounds:
                if not (0 <= x <= width and 0 <= y <= height):
                    raise ValueError(f"Coordinates ({x}, {y}) are outside window bounds ({width}x{height})")

            # Apply DPI scaling and convert to screen coordinates
            screen_x = rect[0] + (x * self._dpi_scale)
            screen_y = rect[1] + (y * self._dpi_scale)
            logger.debug(f"Converting coordinates: window({x}, {y}) -> screen({screen_x}, {screen_y}) with DPI scale {self._dpi_scale}")

            # Move mouse
            pyautogui.moveTo(screen_x, screen_y)
            time.sleep(0.5)

        except Exception as e:
            logger.error(f"Failed to move mouse to position ({x}, {y}): {str(e)}")
            raise Exception(f"Failed to move mouse: {str(e)}")

    def move_mouse(self, x: int, y: int) -> Dict[str, Any]:
        """Move mouse to specific coordinates"""
        logger.debug(f"Moving mouse to position ({x}, {y})")

        try:
            # Ensure SAP window is active before moving
            self._ensure_sap_window_active()

            # Perform move with DPI scaling, using the stored main window handle
            global _main_window_hwnd
            self._move_with_dpi_scaling(_main_window_hwnd, int(x), int(y))

            response = {
                "image": self._take_screenshot()
            }
            logger.debug("Mouse move operation successful")
            return response

        except Exception as e:
            logger.error(f"Failed to move mouse to position ({x}, {y}): {str(e)}")
            raise Exception(f"Failed to move mouse: {str(e)}")
        
    def type_text(self, text: str) -> Dict[str, Any]:
        """Type text at current cursor position"""
        logger.debug(f"Typing text: {text}")

        try:
            # Ensure SAP window is active before typing
            self._ensure_sap_window_active()
            
            # Type text, using the stored main window handle
            pyautogui.typewrite(text)
            time.sleep(0.5)

            response = {
                "image": self._take_screenshot()
            }
            logger.debug("Text input operation successful")
            return response

        except Exception as e:
            logger.error(f"Failed to type text: {str(e)}")
            raise Exception(f"Failed to type text: {str(e)}")

    def scroll_screen(self, direction: str) -> Dict[str, Any]:
        """Scroll the screen up or down"""
        logger.debug(f"Scrolling screen {direction}")

        try:
            # Ensure SAP window is active before scrolling
            self._ensure_sap_window_active()

            # Simulate scroll, using the stored main window handle
            if direction.lower() == "down":
                pyautogui.scroll(-5)  # Negative for down
            else:
                pyautogui.scroll(5)  # Positive for up

            time.sleep(0.5)

            response = {
                "image": self._take_screenshot()
            }
            logger.debug("Scroll operation successful")
            return response

        except Exception as e:
            logger.error(f"Failed to scroll {direction}: {str(e)}")
            raise Exception(f"Failed to scroll: {str(e)}")

    def end_session(self) -> None:
        """End SAP session by killing processes"""
        logger.info("Ending SAP session")
        try:
            subprocess.run(['taskkill', '/F', '/IM', 'saplogon.exe'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(['taskkill', '/F', '/IM', 'sapshcut.exe'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            logger.info("SAP session ended successfully")
        except Exception as e:
            logger.error(f"Error ending session: {str(e)}")
            raise

    def _take_screenshot(self) -> str:
    # def _take_screenshot(self) -> Image:
        """Take screenshot of the active window with cursor and return as base64"""
        try:
            # Get active window
            active_window = pyautogui.getActiveWindow()
            if not active_window:
                raise Exception("No active window found")
                
            # Create monitor dict for mss
            monitor = {
                "top": active_window.top,
                "left": active_window.left,
                "width": active_window.width,
                "height": active_window.height,
            }
            
            # Take screenshot with cursor
            with mss() as sct:
                # Capture with cursor
                screenshot = sct.grab(monitor)
                # Convert to PIL Image
                img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)
            
            # Convert to base64
            buffer = BytesIO()
            img.save(buffer, format="PNG")
            base64string = base64.b64encode(buffer.getvalue()).decode()
            # logger.debug(f"Screenshot Base64: {base64string}" )
            # return img
            return base64string
            
        except Exception as e:
            logger.error(f"Failed to take screenshot: {str(e)}")
            raise Exception(f"Failed to take screenshot: {str(e)}")

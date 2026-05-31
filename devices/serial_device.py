"""Serial console device driver using pyserial."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any, Optional

import serial

from core.exceptions import (
    DeviceAuthenticationError,
    DeviceCommandError,
    DeviceConnectionError,
    DeviceTimeoutError,
    SerialConnectionError,
)
from core.models import DeviceFacts, DeviceHealth, InterfaceInfo
from devices.base import BaseDevice

logger = logging.getLogger(__name__)

# Shared thread pool for all serial operations (pyserial is blocking).
_SERIAL_EXECUTOR = ThreadPoolExecutor(max_workers=16, thread_name_prefix="serial")

# Strip ANSI/VT100 escape sequences from serial output.
_ANSI_RE = re.compile(
    r"\x1b\[\??[0-9;]*[A-Za-z]"  # CSI sequences: ESC[ ... letter
    r"|\x1b[A-Z]"                # Two-char sequences: ESC + letter (e.g. ESC E)
    r"|\x00"                     # NUL bytes
)

# Common CLI prompt patterns across vendors.
_DEFAULT_PROMPT_PATTERN = re.compile(
    r"[\r\n]"                     # newline before prompt
    r"[\w\-\.\/@]+"               # hostname chars
    r"(?:\([^\)]+\))?"            # optional (config) mode
    r"[#>$%]\s*$"                 # prompt character
)


class SerialDevice(BaseDevice):
    """Base serial console device using pyserial for CLI interaction.

    Subclasses should set ``prompt_pattern`` and override parsing methods
    to handle vendor-specific output.  The class mirrors ``SSHDevice``'s
    async pattern by running blocking serial I/O in a ThreadPoolExecutor.
    """

    prompt_pattern: re.Pattern = _DEFAULT_PROMPT_PATTERN
    config_mode_command: str = "configure terminal"
    config_mode_exit: str = "end"
    enable_command: str = "enable"
    disable_paging_command: str = "terminal length 0"
    line_terminator: str = "\n"

    def __init__(
        self,
        host: str = "",
        username: str = "",
        password: str = "",
        port: int = 0,
        device_type: str = "",
        enable_secret: str = "",
        timeout: int = 30,
        serial_port: str = "",
        baudrate: int = 9600,
        bytesize: int = 8,
        parity: str = "N",
        stopbits: float = 1,
        xonxoff: bool = False,
        rtscts: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            host=host or serial_port,
            username=username,
            password=password,
            port=port,
            device_type=device_type,
            enable_secret=enable_secret,
            timeout=timeout,
            **kwargs,
        )
        self.serial_port = serial_port
        self.baudrate = baudrate
        self.bytesize = bytesize
        self.parity = parity
        self.stopbits = stopbits
        self.xonxoff = xonxoff
        self.rtscts = rtscts
        self._serial: Optional[serial.Serial] = None
        self._current_prompt: str = ""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        return asyncio.get_running_loop()

    async def _run_sync(self, func, *args, **kwargs):
        """Run a blocking callable in the serial thread-pool executor."""
        loop = self._get_loop()
        return await loop.run_in_executor(
            _SERIAL_EXECUTOR, partial(func, *args, **kwargs)
        )

    @staticmethod
    def _strip_ansi(text: str) -> str:
        """Remove ANSI/VT100 escape sequences from serial output."""
        return _ANSI_RE.sub("", text)

    def _read_until_prompt(self, timeout: int = 30) -> str:
        """Read serial output until a CLI prompt is detected."""
        assert self._serial is not None
        buffer = ""
        end_time = time.time() + timeout
        self._serial.timeout = 1  # 1-second read chunks

        while time.time() < end_time:
            chunk = self._serial.read(4096)
            if chunk:
                buffer += chunk.decode("utf-8", errors="replace")
                clean = self._strip_ansi(buffer)
                if self.prompt_pattern.search(clean):
                    lines = clean.strip().splitlines()
                    if lines:
                        self._current_prompt = lines[-1].strip()
                    return clean
            else:
                time.sleep(0.1)

        raise TimeoutError(f"Timed out waiting for prompt after {timeout}s")

    def _read_raw_until(self, pattern: str, timeout: int = 10) -> str:
        """Read serial output until *pattern* matches (regex search)."""
        assert self._serial is not None
        buffer = ""
        end_time = time.time() + timeout
        self._serial.timeout = 1

        while time.time() < end_time:
            chunk = self._serial.read(4096)
            if chunk:
                buffer += chunk.decode("utf-8", errors="replace")
                clean = self._strip_ansi(buffer)
                if re.search(pattern, clean, re.IGNORECASE):
                    return clean
            else:
                time.sleep(0.1)

        return self._strip_ansi(buffer)  # return whatever we have

    def _write_line(self, text: str) -> None:
        """Write a line to the serial port with the configured terminator."""
        assert self._serial is not None
        self._serial.write((text + self.line_terminator).encode("utf-8"))
        self._serial.flush()

    def _send_break_signal(self, duration: float = 0.5) -> None:
        """Send a serial break signal (for password recovery)."""
        assert self._serial is not None
        self._serial.send_break(duration=duration)

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open a serial console session."""
        if self._connected and self._serial:
            return
        if not self.serial_port:
            raise SerialConnectionError(
                device_id=self.host,
                serial_port="(none)",
                message="No serial_port specified",
            )
        try:
            def _open():
                return serial.Serial(
                    port=self.serial_port,
                    baudrate=self.baudrate,
                    bytesize=self.bytesize,
                    parity=self.parity,
                    stopbits=self.stopbits,
                    xonxoff=self.xonxoff,
                    rtscts=self.rtscts,
                    timeout=self.timeout,
                )

            self._serial = await self._run_sync(_open)

            def _initial_handshake():
                # Flush stale data and send a carriage return to wake console
                self._serial.reset_input_buffer()
                self._serial.reset_output_buffer()
                time.sleep(0.3)
                self._serial.write(b"\r")
                time.sleep(2)

                # Read whatever came back
                raw = b""
                self._serial.timeout = 2
                chunk = self._serial.read(8192)
                if chunk:
                    raw = chunk

                output = self._strip_ansi(
                    raw.decode("utf-8", errors="replace")
                )

                # Handle "press ENTER to retry" stale auth prompts
                if "ENTER" in output and "retry" in output.lower():
                    self._serial.write(b"\r")
                    time.sleep(2)
                    chunk = self._serial.read(8192)
                    if chunk:
                        output = self._strip_ansi(
                            chunk.decode("utf-8", errors="replace")
                        )

                # Handle "Press any key to continue" banners
                if "press any key" in output.lower():
                    self._serial.write(b" ")
                    time.sleep(2)
                    chunk = self._serial.read(8192)
                    if chunk:
                        output = self._strip_ansi(
                            chunk.decode("utf-8", errors="replace")
                        )

                return output

            initial_output = await self._run_sync(_initial_handshake)

            # Detect login prompt: "User Name:", "Username:", "login:", etc.
            needs_login = self.username and any(
                p in initial_output
                for p in ("ogin:", "sername:", "User Name:")
            )

            if needs_login:
                await self._run_sync(self._handle_login)
            elif not self.prompt_pattern.search(initial_output):
                # Not a login prompt and no CLI prompt detected — try
                # reading a bit longer for a prompt.
                try:
                    await self._run_sync(
                        self._read_until_prompt, self.timeout
                    )
                except TimeoutError:
                    pass  # Best-effort; continue anyway

            # Enter enable mode if secret is provided
            if self.enable_secret and ">" in self._current_prompt:
                await self._run_sync(self._handle_enable)

            # Disable paging
            if self.disable_paging_command:
                await self._run_sync(self._send_paging_disable)

            self._connected = True
            logger.info(
                "Serial connected to %s via %s@%d",
                self.host,
                self.serial_port,
                self.baudrate,
            )

        except serial.SerialException as exc:
            raise SerialConnectionError(
                device_id=self.host,
                serial_port=self.serial_port,
                message=f"Serial port open failed: {exc}",
            ) from exc
        except TimeoutError as exc:
            raise DeviceTimeoutError(
                device_id=self.host,
                message=f"Serial handshake timed out: {exc}",
                timeout_seconds=self.timeout,
            ) from exc
        except DeviceAuthenticationError:
            raise
        except Exception as exc:
            raise DeviceConnectionError(
                device_id=self.host,
                message=f"Serial connection failed: {exc}",
            ) from exc

    def _handle_login(self) -> None:
        """Handle interactive login prompt over serial."""
        assert self._serial is not None
        self._serial.reset_input_buffer()
        time.sleep(0.3)

        # Send username
        self._serial.write(
            (self.username + self.line_terminator).encode("utf-8")
        )
        self._serial.flush()

        # Wait for Password: prompt
        output = self._read_raw_until(r"assword:", timeout=10)

        # Send password
        self._serial.write(
            (self.password + self.line_terminator).encode("utf-8")
        )
        self._serial.flush()

        # Wait for a CLI prompt (success) or auth failure
        post = self._read_raw_until(
            r"[#>]|fail|denied|User Name:|ogin:", timeout=10
        )

        if "fail" in post.lower() or "denied" in post.lower() or "User Name:" in post:
            raise DeviceAuthenticationError(
                device_id=self.host,
                message="Serial authentication failed",
            )

        # Capture the prompt
        lines = post.strip().splitlines()
        if lines:
            self._current_prompt = lines[-1].strip()

    def _handle_enable(self) -> None:
        """Enter enable / privileged mode."""
        self._write_line(self.enable_command)
        time.sleep(0.3)
        output = self._read_until_prompt(timeout=10)
        if "assword:" in output:
            self._write_line(self.enable_secret)
            time.sleep(0.3)
            output = self._read_until_prompt(timeout=10)
        if "#" not in self._current_prompt:
            logger.warning(
                "Enable mode may not have been entered on %s", self.host
            )

    def _send_paging_disable(self) -> None:
        """Disable CLI output paging (best-effort)."""
        try:
            self._serial.reset_input_buffer()
            self._write_line(self.disable_paging_command)
            time.sleep(0.3)
            self._read_until_prompt(timeout=10)
        except (TimeoutError, Exception):
            # Paging disable is best-effort — some devices may reject it
            logger.debug(
                "Paging disable command may have failed on %s", self.host
            )

    async def disconnect(self) -> None:
        """Close the serial port."""
        if self._serial:
            try:
                await self._run_sync(self._serial.close)
            except Exception:
                logger.debug(
                    "Ignoring error during serial disconnect for %s", self.host
                )
            finally:
                self._serial = None
                self._connected = False
                logger.info("Serial disconnected from %s", self.host)

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    async def send_command(self, command: str, timeout: int = 30) -> str:
        """Send a single show / operational command and return output."""
        self._require_connection()
        try:
            def _send():
                self._serial.reset_input_buffer()
                self._write_line(command)
                time.sleep(0.1)
                output = self._read_until_prompt(timeout=timeout)
                return self._strip_command_echo(command, output)

            return await self._run_sync(_send)
        except TimeoutError as exc:
            raise DeviceTimeoutError(
                device_id=self.host,
                message=f"Command timed out: {command!r}",
                timeout_seconds=timeout,
            ) from exc
        except Exception as exc:
            raise DeviceCommandError(
                device_id=self.host,
                message=f"Command failed: {exc}",
                command=command,
            ) from exc

    async def send_config(self, commands: list[str]) -> str:
        """Enter config mode, send commands, exit config mode."""
        self._require_connection()
        try:
            def _send_config():
                all_output: list[str] = []
                # Enter config mode
                self._write_line(self.config_mode_command)
                time.sleep(0.3)
                all_output.append(self._read_until_prompt(timeout=10))

                # Send each command
                for cmd in commands:
                    self._write_line(cmd)
                    time.sleep(0.2)
                    all_output.append(self._read_until_prompt(timeout=10))

                # Exit config mode
                self._write_line(self.config_mode_exit)
                time.sleep(0.3)
                all_output.append(self._read_until_prompt(timeout=10))

                return "\n".join(all_output)

            return await self._run_sync(_send_config)
        except Exception as exc:
            raise DeviceCommandError(
                device_id=self.host,
                message=f"Config push failed: {exc}",
                command="; ".join(commands),
            ) from exc

    async def get_config(self, config_type: str = "running") -> str:
        """Retrieve the device configuration (default: running-config)."""
        cmd_map = {
            "running": "show running-config",
            "startup": "show startup-config",
        }
        cmd = cmd_map.get(config_type, f"show {config_type}-config")
        return await self.send_command(cmd, timeout=60)

    # ------------------------------------------------------------------
    # Default fact / interface / health parsers
    # ------------------------------------------------------------------

    async def get_facts(self) -> DeviceFacts:
        """Generic fact collection -- subclasses should override."""
        output = await self.send_command("show version")
        hostname_match = re.search(r"(\S+)\s+uptime", output, re.IGNORECASE)
        uptime_match = re.search(r"uptime is (.+)", output, re.IGNORECASE)
        return DeviceFacts(
            hostname=hostname_match.group(1) if hostname_match else self.host,
            uptime=uptime_match.group(1).strip() if uptime_match else "",
        )

    async def get_interfaces(self) -> list[InterfaceInfo]:
        """Generic interface list -- subclasses should override."""
        return []

    async def get_health(self) -> DeviceHealth:
        """Generic health -- subclasses should override."""
        return DeviceHealth()

    # ------------------------------------------------------------------
    # Serial-specific public API
    # ------------------------------------------------------------------

    async def send_break(self, duration: float = 0.5) -> str:
        """Send a serial break signal and return any output received."""
        if not self._serial:
            raise DeviceConnectionError(
                device_id=self.host,
                message="Not connected -- call connect() first",
            )

        def _break():
            self._send_break_signal(duration)
            time.sleep(1.0)
            buffer = ""
            self._serial.timeout = 2
            chunk = self._serial.read(4096)
            if chunk:
                buffer = chunk.decode("utf-8", errors="replace")
            return buffer

        return await self._run_sync(_break)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_connection(self) -> None:
        if not self._connected or self._serial is None:
            raise DeviceConnectionError(
                device_id=self.host,
                message="Not connected -- call connect() first",
            )

    @staticmethod
    def _strip_command_echo(command: str, raw_output: str) -> str:
        """Remove the echoed command line and trailing prompt from output."""
        lines = raw_output.splitlines()
        if lines and command.strip() in lines[0]:
            lines = lines[1:]
        if lines:
            lines = lines[:-1]
        return "\n".join(lines).strip()

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} serial_port={self.serial_port} "
            f"baudrate={self.baudrate}>"
        )

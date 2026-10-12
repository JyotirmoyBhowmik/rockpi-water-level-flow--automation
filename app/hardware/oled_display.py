"""
Hardware OLED Status Display Driver (SSD1306 over I2C) for Radxa ROCK Pi S.
Displays physical at-a-glance status:
  - Header / Alarm alert banner
  - Overhead tank water level (% and Litres)
  - Motor pump state (RUNNING, STOPPED, COOLDOWN, LOCKOUT)
  - Wireless garden node link status and active zone watering
  - Motor power telemetry (V, A, W) or network IP address

Supports 128x64 and 128x32 displays on I2C bus 7 (default on ROCK Pi S Header 1).
Fails gracefully when physical display or smbus2 is absent (operates in simulated mode).
"""

import asyncio
import logging
import os
from typing import Any, Dict, Optional
from app import __version__
from app.config import system_config

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import smbus2 as smbus_module
except ImportError:
    try:
        import smbus as smbus_module
    except ImportError:
        smbus_module = None

logger = logging.getLogger("smart_water.display")

# SSD1306 Commands
SSD1306_SETCONTRAST = 0x81
SSD1306_DISPLAYALLON_RESUME = 0xA4
SSD1306_NORMALDISPLAY = 0xA6
SSD1306_INVERTDISPLAY = 0xA7
SSD1306_DISPLAYOFF = 0xAE
SSD1306_DISPLAYON = 0xAF
SSD1306_SETDISPLAYOFFSET = 0xD3
SSD1306_SETCOMPINS = 0xDA
SSD1306_SETVCOMDETECT = 0xDB
SSD1306_SETDISPCLKDIV = 0xD5
SSD1306_SETPRECHARGE = 0xD9
SSD1306_SETMULTIPLEX = 0xA8
SSD1306_SETSTARTLINE = 0x40
SSD1306_MEMORYMODE = 0x20
SSD1306_COLUMNADDR = 0x21
SSD1306_PAGEADDR = 0x22
SSD1306_COMSCANINC = 0xC0
SSD1306_COMSCANDEC = 0xC8
SSD1306_SEGREMAP = 0xA0
SSD1306_CHARGEPUMP = 0x8D


class SSD1306Display:
    """
    Low-level driver for SSD1306 monochrome OLED displays over I2C.
    Handles command initialization, pixel packing, and page-based rendering.
    """

    def __init__(
        self,
        bus_num: int = 7,
        address: int = 0x3C,
        width: int = 128,
        height: int = 64,
        rotate: int = 0,
        mock_bus: Any = None,
    ) -> None:
        self.bus_num = bus_num
        self.address = address
        self.width = width
        self.height = height
        self.rotate = rotate
        self.pages = max(1, height // 8)
        self._bus = mock_bus
        self._owns_bus = mock_bus is None
        self._hardware_available = False
        self._warned = False
        self.last_rendered_image: Optional[Any] = None
        self.last_rendered_buffer: Optional[bytearray] = None

    @property
    def is_hardware_available(self) -> bool:
        """True if physical I2C bus and display are initialized."""
        return self._hardware_available

    def is_bus_available(self) -> bool:
        """Checks if I2C bus device file exists on SBC or mock bus injected."""
        if self._bus is not None:
            return True
        dev_path = f"/dev/i2c-{self.bus_num}"
        return os.path.exists(dev_path) and smbus_module is not None

    def init_display(self) -> bool:
        """
        Connects to I2C bus and executes SSD1306 initialization sequence.
        Returns True if successful, False if running in simulated mode.
        """
        if self._bus is None:
            if not self.is_bus_available():
                if not self._warned:
                    logger.info(
                        "I2C bus /dev/i2c-%d not found or smbus2 not installed. SSD1306 running in simulated mode.",
                        self.bus_num,
                    )
                    self._warned = True
                self._hardware_available = False
                return False

            try:
                assert smbus_module is not None
                self._bus = smbus_module.SMBus(self.bus_num)
            except Exception as exc:
                logger.warning("Could not open I2C bus %d: %s", self.bus_num, exc)
                self._hardware_available = False
                return False

        # SSD1306 Initialization Command List
        com_pins = 0x12 if self.height == 64 else 0x02
        init_cmds = [
            SSD1306_DISPLAYOFF,
            SSD1306_SETDISPCLKDIV, 0x80,
            SSD1306_SETMULTIPLEX, self.height - 1,
            SSD1306_SETDISPLAYOFFSET, 0x00,
            SSD1306_SETSTARTLINE | 0x00,
            SSD1306_CHARGEPUMP, 0x14,
            SSD1306_MEMORYMODE, 0x00,  # Horizontal addressing mode
            SSD1306_SEGREMAP | 0x01,   # Column 127 mapped to SEG0
            SSD1306_COMSCANDEC,        # Remapped scan direction
            SSD1306_SETCOMPINS, com_pins,
            SSD1306_SETCONTRAST, 0xCF,
            SSD1306_SETPRECHARGE, 0xF1,
            SSD1306_SETVCOMDETECT, 0x40,
            SSD1306_DISPLAYALLON_RESUME,
            SSD1306_NORMALDISPLAY,
            SSD1306_DISPLAYON,
        ]

        try:
            for cmd in init_cmds:
                self.write_command(cmd)
            self._hardware_available = True
            logger.info(
                "SSD1306 OLED initialized on I2C bus %d address 0x%02X (%dx%d).",
                self.bus_num,
                self.address,
                self.width,
                self.height,
            )
            self.clear()
            return True
        except Exception as exc:
            logger.warning("Error during SSD1306 initialization: %s", exc)
            self._hardware_available = False
            return False

    def write_command(self, cmd: int) -> None:
        """Sends command byte with control byte 0x00."""
        if self._bus is not None:
            try:
                self._bus.write_byte_data(self.address, 0x00, cmd)
            except Exception as exc:
                logger.debug("I2C write_command error: %s", exc)

    def write_data_chunks(self, data: bytes) -> None:
        """Sends pixel data in 32-byte chunks with control byte 0x40."""
        if self._bus is None:
            return
        chunk_size = 32
        for i in range(0, len(data), chunk_size):
            chunk = list(data[i : i + chunk_size])
            try:
                self._bus.write_i2c_block_data(self.address, 0x40, chunk)
            except Exception as exc:
                logger.debug("I2C write_data error: %s", exc)

    def clear(self) -> None:
        """Clears display buffer to all black."""
        empty = bytearray(self.width * self.pages)
        self.display_buffer(empty)

    def display_buffer(self, buf: bytearray) -> None:
        """Sends raw byte buffer to SSD1306 display RAM."""
        self.last_rendered_buffer = buf
        if not self._hardware_available or self._bus is None:
            return

        try:
            # Set column start and end address
            self.write_command(SSD1306_COLUMNADDR)
            self.write_command(0)
            self.write_command(self.width - 1)

            # Set page start and end address
            self.write_command(SSD1306_PAGEADDR)
            self.write_command(0)
            self.write_command(self.pages - 1)

            # Write buffer stream
            self.write_data_chunks(bytes(buf))
        except Exception as exc:
            logger.warning("Failed to flush SSD1306 buffer: %s", exc)

    def image_to_buffer(self, image: Any) -> bytearray:
        """
        Converts 1-bit monochrome PIL Image to SSD1306 page-addressed format.
        Each page is 8 vertical pixels high by width pixels wide.
        """
        if image.size != (self.width, self.height):
            image = image.resize((self.width, self.height))

        pix = image.load()
        buf = bytearray(self.width * self.pages)

        for p in range(self.pages):
            for x in range(self.width):
                b_val = 0
                for b in range(8):
                    y = p * 8 + b
                    if y < self.height and pix[x, y]:
                        b_val |= 1 << b
                buf[p * self.width + x] = b_val

        return buf

    def display_image(self, image: Any) -> None:
        """Renders PIL image and pushes buffer to display."""
        self.last_rendered_image = image
        buf = self.image_to_buffer(image)
        self.display_buffer(buf)

    def render_status_image(self, state: Dict[str, Any]) -> Any:
        """
        Draws formatted status page for current system state using PIL.
        High contrast, industrial card-style layout.
        """
        if not HAS_PIL:
            return None

        img = Image.new("1", (self.width, self.height), 0)
        draw = ImageDraw.Draw(img)
        font = ImageFont.load_default()

        # Extract system telemetry
        head_end = state.get("head_end", {})
        motor = state.get("motor", {})
        garden = state.get("garden", {})
        level = state.get("level_sensor", {})
        power = state.get("power_meter", {})
        alarms = state.get("alarms", {})

        # Active alarm banner detection
        active_alarms = list(alarms.keys()) if isinstance(alarms, dict) else []

        if self.height == 64:
            # -------------------------------------------------------------
            # 128x64 Standard 5-Line Layout
            # -------------------------------------------------------------
            # Line 1: Header / Alarm Banner (y=0)
            if active_alarms:
                # Inverted solid bar for critical alarm
                alarm_title = f"! ALARM: {active_alarms[0].upper()[:10]} !"
                draw.rectangle((0, 0, self.width - 1, 11), fill=1)
                draw.text((2, 0), alarm_title, font=font, fill=0)
            else:
                draw.text((0, 0), f"SMART WATER v{__version__}", font=font, fill=1)
                draw.line((0, 11, self.width - 1, 11), fill=1)

            # Line 2: Overhead Tank Level (y=13)
            if level.get("available") and level.get("level_pct") is not None:
                pct = int(level.get("level_pct", 0))
                litres = int(level.get("volume_litres", 0) or 0)
                tank_str = f"TANK: {pct}% ({litres}L)"
            else:
                # Fallback to float switches
                tank_low = head_end.get("tank_low_float")
                interlock = head_end.get("interlock_out")
                if tank_low is True:
                    tank_status = "LOW"
                elif interlock is True:
                    tank_status = "NORMAL"
                else:
                    tank_status = "FULL/SAFE"
                tank_str = f"TANK: {tank_status} [FLT]"
            draw.text((0, 13), tank_str, font=font, fill=1)

            # Line 3: Motor Pump State (y=25)
            motor_st = str(motor.get("state", "STOPPED")).upper()
            if motor_st == "RUNNING":
                run_s = motor.get("current_run_seconds", 0) or 0
                mins, secs = divmod(int(run_s), 60)
                motor_str = f"PUMP: RUNNING ({mins:02d}:{secs:02d})"
            elif motor_st in ("LOCKOUT", "FAULT"):
                motor_str = f"PUMP: !{motor_st}!"
            elif motor_st == "COOLDOWN":
                motor_str = "PUMP: COOLDOWN"
            else:
                auto_lbl = "[AUTO]" if system_config.motor.auto_mode else "[MAN]"
                motor_str = f"PUMP: OFF {auto_lbl}"
            draw.text((0, 25), motor_str, font=font, fill=1)

            # Line 4: Wireless Garden Link & Zones (y=37)
            link_st = str(garden.get("link_state", "DISABLED")).upper()
            # Check if any zone is actively irrigating
            watering_zone: Optional[str] = None
            zones_data = garden.get("zones", {})
            for z_key, z_val in zones_data.items():
                if isinstance(z_val, dict) and z_val.get("pump_running"):
                    watering_zone = z_key.replace("zone_", "Z")
                    break

            if watering_zone:
                garden_str = f"GARDEN: {watering_zone} WATERING"
            elif link_st in ("CONNECTED", "ONLINE"):
                garden_str = "GARDEN: LINK OK"
            elif link_st in ("LINK_LOST", "OFFLINE"):
                garden_str = "GARDEN: LINK LOST"
            else:
                garden_str = f"GARDEN: {link_st[:10]}"
            draw.text((0, 37), garden_str, font=font, fill=1)

            # Line 5: Power / Energy or IP Telemetry (y=49)
            if power.get("available") and power.get("voltage_v") is not None:
                v = int(power.get("voltage_v", 0))
                a = float(power.get("current_a", 0.0) or 0.0)
                w = int(power.get("power_w", 0) or 0)
                pwr_str = f"{v}V {a:.1f}A {w}W"
            else:
                bind_ip = system_config.network.bind_address
                pwr_str = f"IP: {bind_ip}"
            draw.text((0, 49), pwr_str, font=font, fill=1)

        else:
            # -------------------------------------------------------------
            # 128x32 Compact 2-Line Layout
            # -------------------------------------------------------------
            if active_alarms:
                draw.text((0, 0), f"! ALARM: {active_alarms[0].upper()[:12]} !", font=font, fill=1)
            else:
                pct = int(level.get("level_pct", 0) or 0)
                mot = str(motor.get("state", "STOPPED"))[:4].upper()
                draw.text((0, 0), f"TNK:{pct}% | PMP:{mot}", font=font, fill=1)

            link_abbr = "OK" if garden.get("link_state") == "CONNECTED" else "LOST"
            draw.text((0, 16), f"GDN:{link_abbr} | v{__version__}", font=font, fill=1)

        if self.rotate in (90, 180, 270):
            img = img.rotate(self.rotate)

        return img

    def close(self) -> None:
        """Closes bus handle and resets hardware status."""
        if self._bus is not None:
            try:
                self._bus.close()
            except Exception:
                pass
            self._bus = None
        self._hardware_available = False


class OLEDDisplayManager:
    """
    Background manager that orchestrates periodic OLED screen refreshes
    from live system telemetry.
    """

    def __init__(self, display: Optional[SSD1306Display] = None) -> None:
        self.display: Optional[SSD1306Display] = display
        self._running: bool = False
        self._task: Optional[asyncio.Task[None]] = None

    async def start(self) -> None:
        """Initializes display hardware and begins background refresh loop."""
        cfg = system_config.display
        if not cfg.enabled:
            logger.info("Local SSD1306 OLED display is disabled in configuration.")
            return

        if self.display is None:
            self.display = SSD1306Display(
                bus_num=cfg.i2c_bus,
                address=cfg.i2c_address,
                width=cfg.width,
                height=cfg.height,
                rotate=cfg.rotate,
            )

        self.display.init_display()

        self._running = True
        self._task = asyncio.create_task(self._refresh_worker(), name="oled_refresh_task")
        logger.info("OLEDDisplayManager started (interval=%.1fs).", cfg.refresh_interval_s)

    async def _refresh_worker(self) -> None:
        """Periodic background refresh loop."""
        cfg = system_config.display
        interval = max(0.5, cfg.refresh_interval_s)

        from app.core.state_manager import state_manager

        while self._running:
            try:
                if self.display is not None and HAS_PIL:
                    state = state_manager.get_state()
                    img = self.display.render_status_image(state)
                    if img is not None:
                        self.display.display_image(img)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("Error in OLED refresh worker: %s", exc)

            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break

    async def stop(self) -> None:
        """Stops background refresh worker and displays shutdown message."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        if self.display is not None and self.display.is_hardware_available and HAS_PIL:
            try:
                # Render clean shutdown banner
                img = Image.new("1", (self.display.width, self.display.height), 0)
                draw = ImageDraw.Draw(img)
                font = ImageFont.load_default()
                draw.text((10, self.display.height // 2 - 6), "SYSTEM OFFLINE", font=font, fill=1)
                self.display.display_image(img)
            except Exception:
                pass
            self.display.close()

        logger.info("OLEDDisplayManager stopped.")


oled_display = OLEDDisplayManager()

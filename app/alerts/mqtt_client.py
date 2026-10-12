"""
MQTT Publisher and Home Assistant Discovery Integration.
Supports:
  - Telemetry publishing (motor, tank level, zones, power meter, weather, alarms)
  - Home Assistant MQTT Discovery auto-configuration
  - Authenticated and signed command handling gated strictly by failsafe_engine
  - Last Will and Testament (LWT) and birth online status tracking
  - Graceful reconnection and error handling (disabled by default)
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, List, Optional
import paho.mqtt.client as mqtt

from app import __version__
from app.config import system_config
from app.core.security import security_manager
from app.core.state_manager import state_manager
from app.engine.failsafes import failsafe_engine
from app.engine.motor_controller import motor_controller
from app.engine.plant_controller import plant_controller
from app.hardware.gateway_driver import garden_gateway
from app.hardware.gpio_driver import head_end_gpio

logger = logging.getLogger("smart_water.mqtt")


class MQTTClientService:
    """
    Manages MQTT connection, Home Assistant discovery publishing,
    periodic state broadcasting, and authenticated command execution.
    """

    def __init__(self) -> None:
        self._client: Optional[mqtt.Client] = None
        self._running: bool = False
        self._publish_task: Optional[asyncio.Task[None]] = None
        self._connected: bool = False
        self._loop_started: bool = False

    async def start(self) -> None:
        """Starts MQTT client background connection and publisher loop if enabled."""
        if self._running:
            return
        cfg = system_config.mqtt
        if not cfg.enabled:
            logger.info("MQTT client is disabled in configuration.")
            return

        self._running = True
        try:
            # Paho MQTT 2.x API version compatibility
            if hasattr(mqtt, "CallbackAPIVersion"):
                self._client = mqtt.Client(
                    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                    client_id=cfg.client_id,
                    protocol=mqtt.MQTTv311,
                )
            else:
                self._client = mqtt.Client(client_id=cfg.client_id)  # type: ignore[call-arg]

            # Credentials
            if cfg.username:
                pw = cfg.password.get_secret_value() if cfg.password else None
                self._client.username_pw_set(cfg.username, pw)

            # TLS setup
            if cfg.tls_enabled:
                self._client.tls_set(ca_certs=cfg.tls_ca_cert)

            # Last Will and Testament
            lwt_topic = f"{cfg.topic_prefix}/status"
            self._client.will_set(lwt_topic, payload="offline", qos=1, retain=True)

            # Callback binding
            self._client.on_connect = self._on_connect
            self._client.on_disconnect = self._on_disconnect
            self._client.on_message = self._on_message

            logger.info("Connecting to MQTT broker at %s:%d...", cfg.broker_host, cfg.broker_port)
            self._client.connect_async(cfg.broker_host, cfg.broker_port, keepalive=60)
            self._client.loop_start()
            self._loop_started = True

            # Start async telemetry publisher loop
            self._publish_task = asyncio.create_task(self._publish_loop(), name="mqtt_publisher")
            logger.info("MQTT client service initialized.")
        except Exception as exc:
            logger.error("Failed to initialize MQTT client: %s", exc)
            self._running = False

    def _on_connect(self, client: mqtt.Client, userdata: Any, flags: Any, rc: Any, properties: Any = None) -> None:
        """Handles MQTT connection established."""
        return_code = rc if isinstance(rc, int) else getattr(rc, "value", rc)
        if return_code == 0:
            logger.info("Connected to MQTT broker successfully.")
            self._connected = True
            cfg = system_config.mqtt

            # Publish birth message
            client.publish(f"{cfg.topic_prefix}/status", payload="online", qos=1, retain=True)

            # Subscribe to command topics
            cmd_topic = f"{cfg.topic_prefix}/cmd/#"
            client.subscribe(cmd_topic, qos=1)
            logger.info("Subscribed to MQTT command topic: %s", cmd_topic)

            # Publish Home Assistant Discovery configurations if enabled
            if cfg.ha_discovery_enabled:
                self._publish_ha_discovery()
        else:
            logger.error("MQTT connection failed with code: %s", return_code)
            self._connected = False

    def _on_disconnect(self, client: mqtt.Client, userdata: Any, rc: Any, properties: Any = None) -> None:
        """Handles MQTT disconnection."""
        self._connected = False
        logger.warning("Disconnected from MQTT broker (rc=%s). Auto-reconnect active.", rc)

    def _publish_ha_discovery(self) -> None:
        """Publishes Home Assistant MQTT Discovery configuration payloads."""
        if not self._client or not self._connected:
            return

        cfg = system_config.mqtt
        disc_prefix = cfg.ha_discovery_prefix
        topic_prefix = cfg.topic_prefix
        node_id = "smart_water"

        device_info = {
            "identifiers": ["radxa_rockpi_smart_water"],
            "name": "Smart Water Controller",
            "model": "Radxa ROCK Pi S",
            "manufacturer": "Radxa",
            "sw_version": __version__,
        }
        availability = {
            "topic": f"{topic_prefix}/status",
            "payload_available": "online",
            "payload_not_available": "offline",
        }

        # Discovery entity templates
        entities: List[Dict[str, Any]] = [
            # 1. Binary Sensors
            {
                "comp": "binary_sensor",
                "obj_id": "motor_running",
                "config": {
                    "name": "Motor Running",
                    "state_topic": f"{topic_prefix}/state/motor",
                    "value_template": "{{ value_json.running }}",
                    "payload_on": True,
                    "payload_off": False,
                    "device_class": "running",
                },
            },
            {
                "comp": "binary_sensor",
                "obj_id": "tank_low_float",
                "config": {
                    "name": "Tank Low Float",
                    "state_topic": f"{topic_prefix}/state/head_end",
                    "value_template": "{{ value_json.tank_low_float }}",
                    "payload_on": True,
                    "payload_off": False,
                    "device_class": "problem",
                },
            },
            {
                "comp": "binary_sensor",
                "obj_id": "interlock_closed",
                "config": {
                    "name": "Hardware Interlock Chain",
                    "state_topic": f"{topic_prefix}/state/head_end",
                    "value_template": "{{ value_json.interlock_out }}",
                    "payload_on": True,
                    "payload_off": False,
                    "device_class": "safety",
                },
            },
            {
                "comp": "binary_sensor",
                "obj_id": "leak_detected",
                "config": {
                    "name": "Head-End Leak Detected",
                    "state_topic": f"{topic_prefix}/state/head_end",
                    "value_template": "{{ value_json.leak_detected }}",
                    "payload_on": True,
                    "payload_off": False,
                    "device_class": "problem",
                },
            },
            {
                "comp": "binary_sensor",
                "obj_id": "wiring_fault",
                "config": {
                    "name": "Wiring Fault Latched",
                    "state_topic": f"{topic_prefix}/state/head_end",
                    "value_template": "{{ value_json.wiring_fault_latched }}",
                    "payload_on": True,
                    "payload_off": False,
                    "device_class": "problem",
                },
            },
            {
                "comp": "binary_sensor",
                "obj_id": "garden_reservoir_wet",
                "config": {
                    "name": "Garden Reservoir Wet",
                    "state_topic": f"{topic_prefix}/state/garden",
                    "value_template": "{{ value_json.reservoir_wet }}",
                    "payload_on": True,
                    "payload_off": False,
                    "device_class": "moisture",
                },
            },
            # 2. Sensors
            {
                "comp": "sensor",
                "obj_id": "tank_level_pct",
                "config": {
                    "name": "Overhead Tank Level",
                    "state_topic": f"{topic_prefix}/state/level",
                    "value_template": "{{ value_json.level_pct }}",
                    "unit_of_measurement": "%",
                    "icon": "mdi:water-percent",
                },
            },
            {
                "comp": "sensor",
                "obj_id": "tank_volume_litres",
                "config": {
                    "name": "Overhead Tank Volume",
                    "state_topic": f"{topic_prefix}/state/level",
                    "value_template": "{{ value_json.volume_litres }}",
                    "unit_of_measurement": "L",
                    "device_class": "water",
                },
            },
            {
                "comp": "sensor",
                "obj_id": "mains_voltage",
                "config": {
                    "name": "AC Mains Voltage",
                    "state_topic": f"{topic_prefix}/state/power",
                    "value_template": "{{ value_json.voltage_v }}",
                    "unit_of_measurement": "V",
                    "device_class": "voltage",
                },
            },
            {
                "comp": "sensor",
                "obj_id": "motor_current",
                "config": {
                    "name": "Motor Current",
                    "state_topic": f"{topic_prefix}/state/power",
                    "value_template": "{{ value_json.current_a }}",
                    "unit_of_measurement": "A",
                    "device_class": "current",
                },
            },
            {
                "comp": "sensor",
                "obj_id": "motor_power",
                "config": {
                    "name": "Motor Active Power",
                    "state_topic": f"{topic_prefix}/state/power",
                    "value_template": "{{ value_json.power_w }}",
                    "unit_of_measurement": "W",
                    "device_class": "power",
                },
            },
            {
                "comp": "sensor",
                "obj_id": "motor_energy_kwh",
                "config": {
                    "name": "Motor Energy Total",
                    "state_topic": f"{topic_prefix}/state/power",
                    "value_template": "{{ value_json.energy_kwh }}",
                    "unit_of_measurement": "kWh",
                    "device_class": "energy",
                    "state_class": "total_increasing",
                },
            },
            {
                "comp": "sensor",
                "obj_id": "active_alarms_count",
                "config": {
                    "name": "Active Safety Alarms",
                    "state_topic": f"{topic_prefix}/state/alarms",
                    "value_template": "{{ value_json.count }}",
                    "icon": "mdi:alert-circle-outline",
                },
            },
            # 3. Switches / Control
            {
                "comp": "switch",
                "obj_id": "motor_switch",
                "config": {
                    "name": "Overhead Tank Pump",
                    "state_topic": f"{topic_prefix}/state/motor",
                    "value_template": "{{ value_json.state }}",
                    "state_on": "RUNNING",
                    "state_off": "OFF",
                    "command_topic": f"{topic_prefix}/cmd/motor/set",
                    "icon": "mdi:pump",
                },
            },
        ]

        # Add per-zone sensors and switches
        for i in range(1, 5):
            entities.extend([
                {
                    "comp": "sensor",
                    "obj_id": f"zone_{i}_moisture",
                    "config": {
                        "name": f"Zone {i} Moisture",
                        "state_topic": f"{topic_prefix}/state/zones",
                        "value_template": f"{{{{ value_json.zone_{i}.moisture_pct }}}}",
                        "unit_of_measurement": "%",
                        "device_class": "moisture",
                    },
                },
                {
                    "comp": "sensor",
                    "obj_id": f"zone_{i}_daily_litres",
                    "config": {
                        "name": f"Zone {i} Daily Litres",
                        "state_topic": f"{topic_prefix}/state/zones",
                        "value_template": f"{{{{ value_json.zone_{i}.daily_litres }}}}",
                        "unit_of_measurement": "L",
                        "device_class": "water",
                    },
                },
                {
                    "comp": "switch",
                    "obj_id": f"zone_{i}_pump",
                    "config": {
                        "name": f"Zone {i} Pump",
                        "state_topic": f"{topic_prefix}/state/zones",
                        "value_template": f"{{{{ value_json.zone_{i}.pump_running }}}}",
                        "state_on": True,
                        "state_off": False,
                        "command_topic": f"{topic_prefix}/cmd/zone_{i}/set",
                        "icon": "mdi:sprinkler",
                    },
                },
            ])

        for ent in entities:
            topic = f"{disc_prefix}/{ent['comp']}/{node_id}/{ent['obj_id']}/config"
            cfg_payload = dict(ent["config"])
            cfg_payload["unique_id"] = f"smart_water_{ent['obj_id']}"
            cfg_payload["device"] = device_info
            cfg_payload["availability"] = availability
            self._client.publish(topic, payload=json.dumps(cfg_payload), qos=1, retain=True)

        logger.info("Published %d Home Assistant MQTT discovery entities.", len(entities))

    async def _publish_loop(self) -> None:
        """Asynchronously publishes state telemetry to MQTT on configured interval."""
        cfg = system_config.mqtt
        interval = cfg.publish_interval_s

        while self._running:
            try:
                await asyncio.sleep(interval)
                if not self._connected or not self._client:
                    continue

                state = state_manager.get_state()
                prefix = cfg.topic_prefix

                # 1. Motor State
                motor = state.get("motor", {})
                self._client.publish(
                    f"{prefix}/state/motor",
                    payload=json.dumps({
                        "state": motor.get("state", "OFF"),
                        "running": motor.get("state") == "RUNNING",
                        "current_run_s": motor.get("current_run_seconds", 0),
                        "auto_mode": motor.get("auto_mode", False),
                        "manual_locked": motor.get("manual_locked", False),
                    }),
                    qos=0,
                )

                # 2. Head-End State
                he = state.get("head_end", {})
                self._client.publish(
                    f"{prefix}/state/head_end",
                    payload=json.dumps({
                        "tank_low_float": he.get("tank_low_float"),
                        "interlock_out": he.get("interlock_out"),
                        "interlock_cause": he.get("interlock_cause"),
                        "leak_detected": he.get("leak_detected"),
                        "wiring_fault_latched": he.get("wiring_fault_latched", False),
                    }),
                    qos=0,
                )

                # 3. Garden Node State
                garden = state.get("garden", {})
                self._client.publish(
                    f"{prefix}/state/garden",
                    payload=json.dumps({
                        "node_online": garden.get("node_online", False),
                        "reservoir_wet": garden.get("reservoir_wet"),
                        "rssi": garden.get("rssi"),
                        "uptime_s": garden.get("uptime_s"),
                        "env": garden.get("env", {}),
                    }),
                    qos=0,
                )

                # 4. Zones State
                self._client.publish(
                    f"{prefix}/state/zones",
                    payload=json.dumps(garden.get("zones", {})),
                    qos=0,
                )

                # 5. Power Meter State
                pm = state.get("power_meter", {})
                self._client.publish(
                    f"{prefix}/state/power",
                    payload=json.dumps(pm),
                    qos=0,
                )

                # 6. Level Sensor State
                ls = state.get("level_sensor", {})
                self._client.publish(
                    f"{prefix}/state/level",
                    payload=json.dumps(ls),
                    qos=0,
                )

                # 7. Alarms State
                alarms = state.get("alarms", {})
                self._client.publish(
                    f"{prefix}/state/alarms",
                    payload=json.dumps({
                        "count": len(alarms),
                        "alarms": alarms,
                    }),
                    qos=0,
                )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Error in MQTT telemetry publish loop: %s", exc)

    def _on_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        """Processes incoming commands from subscribed MQTT command topics."""
        topic: str = msg.topic
        payload_str: str = ""
        try:
            payload_str = msg.payload.decode("utf-8").strip()
        except Exception:
            logger.warning("Received invalid binary payload on MQTT topic %s", topic)
            return

        # Hand off asynchronous execution to the active event loop
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._handle_command(topic, payload_str))
        except RuntimeError:
            pass

    async def _handle_command(self, topic: str, raw_payload: str) -> None:
        """
        Validates authentication and executes remote control command strictly through failsafes.
        """
        cfg = system_config.mqtt
        prefix = cfg.topic_prefix
        subtopic = topic[len(prefix):].lstrip("/")

        logger.info("Received MQTT command on '%s': %s", topic, raw_payload)

        # Parse payload: JSON or text
        data: Dict[str, Any] = {}
        if raw_payload.startswith("{") and raw_payload.endswith("}"):
            try:
                data = json.loads(raw_payload)
            except Exception:
                data = {"action": raw_payload}
        else:
            data = {"action": raw_payload}

        action = str(data.get("action", "")).upper()

        # 1. Authentication Check
        if cfg.command_auth_required:
            token = data.get("token") or data.get("auth")
            if not token or not security_manager.verify_token(str(token)):
                logger.warning("Unauthorized MQTT command rejected on '%s' (invalid or missing token)", topic)
                self._publish_response(topic, success=False, error="UNAUTHORIZED", message="Invalid or missing auth token")
                return

        # 2. Command Dispatch & Failsafe Gating
        try:
            if subtopic in ("cmd/motor/set", "cmd/motor"):
                await self._execute_motor_command(topic, action)
            elif subtopic.startswith("cmd/zone_") and subtopic.endswith("/set"):
                # Extract zone name e.g. cmd/zone_1/set -> zone_1
                zone_id = subtopic.split("/")[1]
                duration_s = int(data.get("duration_s", 15))
                await self._execute_zone_command(topic, zone_id, action, duration_s)
            elif subtopic in ("cmd/estop", "cmd/emergency_stop"):
                logger.critical("Emergency STOP commanded via MQTT topic %s!", topic)
                await head_end_gpio.force_all_outputs_off()
                await garden_gateway.send_stop_all()
                self._publish_response(topic, success=True, message="All outputs forced OFF (EMERGENCY STOP)")
            else:
                logger.warning("Unrecognized MQTT command subtopic: %s", subtopic)
                self._publish_response(topic, success=False, error="UNKNOWN_TOPIC", message=f"Unknown command: {subtopic}")
        except Exception as exc:
            logger.error("Error executing MQTT command on %s: %s", topic, exc)
            self._publish_response(topic, success=False, error="INTERNAL_ERROR", message=str(exc))

    async def _execute_motor_command(self, topic: str, action: str) -> None:
        """Executes motor start or stop through failsafe_engine."""
        if action in ("START", "ON", "1", "TRUE"):
            can_start, reason = failsafe_engine.can_start_motor()
            if not can_start:
                logger.warning("MQTT motor start rejected by failsafe: %s", reason)
                self._publish_response(topic, success=False, error="FAILSAFE_BLOCKED", message=reason)
                return

            started = await motor_controller.start_motor(reason="mqtt_command")
            if started:
                self._publish_response(topic, success=True, message="Motor contactor energized")
            else:
                self._publish_response(topic, success=False, error="START_FAILED", message="Contactor failed to close")
        elif action in ("STOP", "OFF", "0", "FALSE"):
            stopped = await motor_controller.stop_motor(reason="mqtt_command")
            self._publish_response(topic, success=stopped, message="Motor stopped")
        else:
            self._publish_response(topic, success=False, error="INVALID_ACTION", message=f"Invalid action: {action}")

    async def _execute_zone_command(self, topic: str, zone_id: str, action: str, duration_s: int) -> None:
        """Executes zone watering through plant_controller and failsafe gates."""
        if action in ("START", "ON", "1", "TRUE"):
            can_water, reason = failsafe_engine.can_water_zone(zone_id, is_manual=True)
            if not can_water:
                logger.warning("MQTT zone pulse for %s rejected by failsafe: %s", zone_id, reason)
                self._publish_response(topic, success=False, error="FAILSAFE_BLOCKED", message=reason)
                return

            success, result_reason = await plant_controller.pulse_zone(
                zone_id=zone_id,
                duration_s=min(duration_s, 60),
                source="mqtt_command",
            )
            self._publish_response(topic, success=success, message=result_reason)
        elif action in ("STOP", "OFF", "0", "FALSE"):
            stopped = await plant_controller.stop_zone_pulse(zone_id=zone_id)
            self._publish_response(topic, success=stopped, message=f"Zone {zone_id} stopped")
        else:
            self._publish_response(topic, success=False, error="INVALID_ACTION", message=f"Invalid action: {action}")

    def _publish_response(
        self,
        cmd_topic: str,
        success: bool,
        message: str = "",
        error: Optional[str] = None,
    ) -> None:
        """Publishes acknowledgment response to MQTT."""
        if not self._client or not self._connected:
            return
        cfg = system_config.mqtt
        resp_topic = f"{cfg.topic_prefix}/cmd/response"
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cmd_topic": cmd_topic,
            "success": success,
            "message": message,
        }
        if error:
            payload["error"] = error
        self._client.publish(resp_topic, payload=json.dumps(payload), qos=1)

    async def stop(self) -> None:
        """Stops telemetry loop and cleanly disconnects MQTT client."""
        self._running = False
        if self._publish_task and not self._publish_task.done():
            self._publish_task.cancel()
            try:
                await self._publish_task
            except asyncio.CancelledError:
                pass

        if self._client:
            try:
                cfg = system_config.mqtt
                if self._connected:
                    self._client.publish(f"{cfg.topic_prefix}/status", payload="offline", qos=1, retain=True)
                if self._loop_started:
                    self._client.loop_stop()
                self._client.disconnect()
            except Exception as exc:
                logger.warning("Error stopping MQTT client: %s", exc)
            self._client = None

        self._connected = False
        self._loop_started = False
        logger.info("MQTT client service stopped.")


mqtt_client = MQTTClientService()

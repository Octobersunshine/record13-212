import threading
import time
import requests
from datetime import datetime, timedelta
from typing import Dict, Optional, Callable, List
from dataclasses import dataclass, field
from flask import Flask, request, jsonify


@dataclass
class Device:
    device_id: str
    last_heartbeat: datetime
    status: str = "online"
    offline_time: Optional[datetime] = None
    last_alert_time: Optional[datetime] = None
    alert_count: int = 0
    metadata: Dict[str, str] = field(default_factory=dict)


@dataclass
class AlertConfig:
    webhook_url: Optional[str] = None
    timeout_minutes: int = 5
    cooldown_minutes: int = 10
    max_alerts: int = 3


class HeartbeatService:
    def __init__(self, alert_config: Optional[AlertConfig] = None):
        self.devices: Dict[str, Device] = {}
        self.alert_config = alert_config or AlertConfig()
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._check_thread: Optional[threading.Thread] = None
        self._alert_callbacks: List[Callable[[Device], None]] = []

    def add_alert_callback(self, callback: Callable[[Device], None]) -> None:
        self._alert_callbacks.append(callback)

    def process_heartbeat(self, device_id: str, metadata: Optional[Dict[str, str]] = None) -> Dict:
        if not device_id:
            raise ValueError("device_id is required")

        now = datetime.now()
        with self._lock:
            if device_id in self.devices:
                device = self.devices[device_id]
                was_offline = device.status == "offline"
                device.last_heartbeat = now
                device.status = "online"
                device.offline_time = None
                if was_offline:
                    device.alert_count = 0
                    device.last_alert_time = None
                    self._send_alert(device, "back_online")
            else:
                device = Device(
                    device_id=device_id,
                    last_heartbeat=now,
                    status="online",
                    metadata=metadata or {}
                )
                self.devices[device_id] = device

            if metadata:
                device.metadata.update(metadata)

            return {
                "device_id": device.device_id,
                "status": device.status,
                "last_heartbeat": device.last_heartbeat.isoformat(),
                "next_heartbeat_due": (now + timedelta(minutes=self.alert_config.timeout_minutes)).isoformat()
            }

    def get_device_status(self, device_id: str) -> Optional[Dict]:
        with self._lock:
            device = self.devices.get(device_id)
            if not device:
                return None
            return self._device_to_dict(device)

    def get_all_devices(self, status_filter: Optional[str] = None) -> List[Dict]:
        with self._lock:
            devices = list(self.devices.values())
            if status_filter:
                devices = [d for d in devices if d.status == status_filter]
            return [self._device_to_dict(d) for d in devices]

    def check_timeouts(self) -> List[Device]:
        offline_devices = []
        now = datetime.now()
        timeout_threshold = now - timedelta(minutes=self.alert_config.timeout_minutes)

        with self._lock:
            for device in self.devices.values():
                if device.status == "online" and device.last_heartbeat < timeout_threshold:
                    device.status = "offline"
                    device.offline_time = now
                    offline_devices.append(device)
                    self._send_alert(device, "offline")
                elif device.status == "offline" and device.last_heartbeat < timeout_threshold:
                    if self._should_send_alert(device):
                        self._send_alert(device, "offline")

        return offline_devices

    def _should_send_alert(self, device: Device) -> bool:
        if device.alert_count >= self.alert_config.max_alerts:
            return False

        if device.last_alert_time is None:
            return True

        cooldown = timedelta(minutes=self.alert_config.cooldown_minutes)
        return datetime.now() >= device.last_alert_time + cooldown

    def _send_alert(self, device: Device, alert_type: str) -> None:
        if alert_type == "offline":
            if device.alert_count >= self.alert_config.max_alerts:
                return

            device.alert_count += 1
            device.last_alert_time = datetime.now()

        alert_payload = {
            "type": alert_type,
            "device_id": device.device_id,
            "timestamp": datetime.now().isoformat(),
            "last_heartbeat": device.last_heartbeat.isoformat(),
            "status": device.status,
            "alert_count": device.alert_count,
            "metadata": device.metadata
        }

        if self.alert_config.webhook_url:
            try:
                requests.post(
                    self.alert_config.webhook_url,
                    json=alert_payload,
                    timeout=5
                )
            except Exception:
                pass

        for callback in self._alert_callbacks:
            try:
                callback(device)
            except Exception:
                pass

    def _device_to_dict(self, device: Device) -> Dict:
        now = datetime.now()
        return {
            "device_id": device.device_id,
            "status": device.status,
            "last_heartbeat": device.last_heartbeat.isoformat(),
            "offline_time": device.offline_time.isoformat() if device.offline_time else None,
            "last_alert_time": device.last_alert_time.isoformat() if device.last_alert_time else None,
            "seconds_since_last_heartbeat": int((now - device.last_heartbeat).total_seconds()),
            "alert_count": device.alert_count,
            "metadata": device.metadata
        }

    def start_monitor(self, check_interval_seconds: int = 30) -> None:
        if self._check_thread and self._check_thread.is_alive():
            return

        self._stop_event.clear()

        def monitor_loop():
            while not self._stop_event.is_set():
                try:
                    self.check_timeouts()
                except Exception:
                    pass
                time.sleep(check_interval_seconds)

        self._check_thread = threading.Thread(target=monitor_loop, daemon=True)
        self._check_thread.start()

    def stop_monitor(self) -> None:
        self._stop_event.set()
        if self._check_thread:
            self._check_thread.join(timeout=5)


def create_app(alert_config: Optional[AlertConfig] = None) -> Flask:
    app = Flask(__name__)
    service = HeartbeatService(alert_config)
    service.start_monitor()

    @app.route('/api/heartbeat', methods=['POST'])
    def heartbeat():
        data = request.get_json() or {}
        device_id = data.get('device_id')
        metadata = data.get('metadata')

        try:
            result = service.process_heartbeat(device_id, metadata)
            return jsonify({"code": 0, "message": "success", "data": result}), 200
        except ValueError as e:
            return jsonify({"code": 400, "message": str(e)}), 400

    @app.route('/api/devices', methods=['GET'])
    def list_devices():
        status_filter = request.args.get('status')
        devices = service.get_all_devices(status_filter)
        return jsonify({"code": 0, "message": "success", "data": devices, "count": len(devices)}), 200

    @app.route('/api/devices/<device_id>', methods=['GET'])
    def get_device(device_id):
        device = service.get_device_status(device_id)
        if not device:
            return jsonify({"code": 404, "message": "device not found"}), 404
        return jsonify({"code": 0, "message": "success", "data": device}), 200

    @app.route('/api/health', methods=['GET'])
    def health():
        return jsonify({"code": 0, "message": "ok", "data": {"timestamp": datetime.now().isoformat()}}), 200

    app.config['HEARTBEAT_SERVICE'] = service
    return app


if __name__ == '__main__':
    config = AlertConfig(
        timeout_minutes=5,
        cooldown_minutes=10,
        max_alerts=3
    )
    app = create_app(config)
    app.run(host='0.0.0.0', port=5000, debug=False)

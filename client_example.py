import requests
import time
import random
from datetime import datetime


class HeartbeatClient:
    def __init__(self, base_url: str = "http://localhost:5000"):
        self.base_url = base_url

    def send_heartbeat(self, device_id: str, metadata: dict = None) -> dict:
        payload = {"device_id": device_id}
        if metadata:
            payload["metadata"] = metadata

        try:
            response = requests.post(
                f"{self.base_url}/api/heartbeat",
                json=payload,
                timeout=5
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"[ERROR] Heartbeat failed: {e}")
            return {"code": -1, "message": str(e)}

    def get_device_status(self, device_id: str) -> dict:
        try:
            response = requests.get(
                f"{self.base_url}/api/devices/{device_id}",
                timeout=5
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"[ERROR] Get status failed: {e}")
            return {"code": -1, "message": str(e)}

    def list_all_devices(self, status: str = None) -> dict:
        params = {"status": status} if status else {}
        try:
            response = requests.get(
                f"{self.base_url}/api/devices",
                params=params,
                timeout=5
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"[ERROR] List devices failed: {e}")
            return {"code": -1, "message": str(e)}

    def health_check(self) -> bool:
        try:
            response = requests.get(f"{self.base_url}/api/health", timeout=3)
            return response.status_code == 200
        except requests.RequestException:
            return False


def simulate_devices():
    print("=" * 60)
    print("Device Heartbeat Simulation")
    print("=" * 60)

    client = HeartbeatClient()

    if not client.health_check():
        print("[ERROR] Server is not running. Please start heartbeat_service.py first.")
        print("  Run: python heartbeat_service.py")
        return

    print("[OK] Server is running\n")

    devices = [
        {"id": "gateway_001", "location": "floor_1", "ip": "192.168.1.10"},
        {"id": "gateway_002", "location": "floor_2", "ip": "192.168.1.11"},
        {"id": "sensor_001", "location": "warehouse", "ip": "192.168.1.20"},
        {"id": "sensor_002", "location": "warehouse", "ip": "192.168.1.21"},
        {"id": "sensor_003", "location": "office", "ip": "192.168.1.22"},
    ]

    print(f"[INFO] Simulating {len(devices)} devices...\n")

    heartbeat_interval = 30
    iteration = 0

    try:
        while True:
            iteration += 1
            print(f"\n--- Iteration {iteration} ({datetime.now().strftime('%H:%M:%S')}) ---")

            for dev in devices:
                if random.random() < 0.1:
                    print(f"  [SKIP] {dev['id']} skipped heartbeat (simulated failure)")
                    continue

                result = client.send_heartbeat(
                    dev["id"],
                    metadata={"location": dev["location"], "ip": dev["ip"]}
                )

                if result["code"] == 0:
                    data = result["data"]
                    print(f"  [OK] {dev['id']} - status: {data['status']}")
                else:
                    print(f"  [FAIL] {dev['id']} - {result.get('message')}")

            print("\nCurrent device status:")
            all_devices = client.list_all_devices()
            if all_devices["code"] == 0:
                online = sum(1 for d in all_devices["data"] if d["status"] == "online")
                offline = sum(1 for d in all_devices["data"] if d["status"] == "offline")
                print(f"  Total: {all_devices['count']} | Online: {online} | Offline: {offline}")

                if offline > 0:
                    print("  Offline devices:")
                    for d in all_devices["data"]:
                        if d["status"] == "offline":
                            print(f"    - {d['device_id']} (offline for {d['seconds_since_last_heartbeat']}s)")

            print(f"\nSleeping {heartbeat_interval} seconds... (Ctrl+C to exit)")
            time.sleep(heartbeat_interval)

    except KeyboardInterrupt:
        print("\n\n[INFO] Simulation stopped by user")
        print("\nFinal status:")
        all_devices = client.list_all_devices()
        if all_devices["code"] == 0:
            for d in all_devices["data"]:
                print(f"  {d['device_id']}: {d['status']} | last_heartbeat: {d['last_heartbeat']}")


def quick_start_example():
    print("=" * 60)
    print("Quick Start Example")
    print("=" * 60)

    client = HeartbeatClient()

    if not client.health_check():
        print("[ERROR] Server is not running.")
        return

    print("\n1. Send heartbeat for a new device:")
    result = client.send_heartbeat("device_test_001", {"type": "temperature_sensor", "firmware": "v1.2.3"})
    print(f"   Result: {result}")

    print("\n2. Get device status:")
    status = client.get_device_status("device_test_001")
    print(f"   Status: {status}")

    print("\n3. List all devices:")
    devices = client.list_all_devices()
    print(f"   Devices count: {devices.get('count')}")

    print("\n4. List only online devices:")
    online_devices = client.list_all_devices(status="online")
    print(f"   Online devices: {[d['device_id'] for d in online_devices.get('data', [])]}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "simulate":
        simulate_devices()
    else:
        quick_start_example()

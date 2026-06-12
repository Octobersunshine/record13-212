import unittest
import time
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
from heartbeat_service import HeartbeatService, AlertConfig, Device, create_app


class TestHeartbeatService(unittest.TestCase):

    def setUp(self):
        self.config = AlertConfig(
            timeout_minutes=1,
            cooldown_minutes=0,
            max_alerts=3
        )
        self.service = HeartbeatService(self.config)

    def test_process_heartbeat_new_device(self):
        result = self.service.process_heartbeat("device_001", {"ip": "192.168.1.100"})
        self.assertEqual(result["device_id"], "device_001")
        self.assertEqual(result["status"], "online")
        self.assertIn("last_heartbeat", result)

        device = self.service.devices["device_001"]
        self.assertEqual(device.metadata["ip"], "192.168.1.100")
        self.assertEqual(device.status, "online")

    def test_process_heartbeat_existing_device(self):
        self.service.process_heartbeat("device_001")
        time.sleep(0.01)
        result = self.service.process_heartbeat("device_001", {"status": "active"})
        self.assertEqual(result["status"], "online")
        self.assertEqual(self.service.devices["device_001"].metadata["status"], "active")

    def test_process_heartbeat_missing_device_id(self):
        with self.assertRaises(ValueError):
            self.service.process_heartbeat("")

    def test_get_device_status(self):
        self.service.process_heartbeat("device_001", {"location": "room_a"})
        status = self.service.get_device_status("device_001")
        self.assertEqual(status["device_id"], "device_001")
        self.assertEqual(status["status"], "online")
        self.assertEqual(status["metadata"]["location"], "room_a")
        self.assertIn("seconds_since_last_heartbeat", status)

    def test_get_device_status_not_found(self):
        status = self.service.get_device_status("nonexistent")
        self.assertIsNone(status)

    def test_get_all_devices(self):
        self.service.process_heartbeat("device_001")
        self.service.process_heartbeat("device_002")
        devices = self.service.get_all_devices()
        self.assertEqual(len(devices), 2)

    def test_get_all_devices_with_filter(self):
        self.service.process_heartbeat("device_001")
        self.service.process_heartbeat("device_002")

        self.service.devices["device_002"].status = "offline"

        online_devices = self.service.get_all_devices("online")
        self.assertEqual(len(online_devices), 1)
        self.assertEqual(online_devices[0]["device_id"], "device_001")

        offline_devices = self.service.get_all_devices("offline")
        self.assertEqual(len(offline_devices), 1)
        self.assertEqual(offline_devices[0]["device_id"], "device_002")

    def test_check_timeouts_no_timeout(self):
        self.service.process_heartbeat("device_001")
        offline = self.service.check_timeouts()
        self.assertEqual(len(offline), 0)
        self.assertEqual(self.service.devices["device_001"].status, "online")

    def test_check_timeouts_device_offline(self):
        self.service.process_heartbeat("device_001")

        device = self.service.devices["device_001"]
        device.last_heartbeat = datetime.now() - timedelta(minutes=2)

        offline = self.service.check_timeouts()
        self.assertEqual(len(offline), 1)
        self.assertEqual(offline[0].device_id, "device_001")
        self.assertEqual(device.status, "offline")
        self.assertIsNotNone(device.offline_time)
        self.assertEqual(device.alert_count, 1)

    def test_alert_callback_triggered(self):
        callback_called = []

        def callback(device):
            callback_called.append(device.device_id)

        self.service.add_alert_callback(callback)
        self.service.process_heartbeat("device_001")

        device = self.service.devices["device_001"]
        device.last_heartbeat = datetime.now() - timedelta(minutes=2)

        self.service.check_timeouts()
        self.assertEqual(len(callback_called), 1)
        self.assertEqual(callback_called[0], "device_001")

    def test_alert_webhook_triggered(self):
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch('requests.post', return_value=mock_response) as mock_post:
            self.config.webhook_url = "http://example.com/alert"
            self.service.process_heartbeat("device_001")

            device = self.service.devices["device_001"]
            device.last_heartbeat = datetime.now() - timedelta(minutes=2)

            self.service.check_timeouts()

            mock_post.assert_called_once()
            call_args = mock_post.call_args
            self.assertEqual(call_args[0][0], "http://example.com/alert")
            self.assertEqual(call_args[1]["json"]["type"], "offline")
            self.assertEqual(call_args[1]["json"]["device_id"], "device_001")

    def test_max_alerts_limit(self):
        self.service.process_heartbeat("device_001")
        device = self.service.devices["device_001"]
        device.last_heartbeat = datetime.now() - timedelta(minutes=2)

        for i in range(5):
            self.service.check_timeouts()
            device.last_heartbeat = datetime.now() - timedelta(minutes=2)

        self.assertEqual(device.alert_count, 3)

    def test_device_comes_back_online(self):
        callback_events = []

        def callback(device):
            callback_events.append((device.device_id, device.status))

        self.service.add_alert_callback(callback)
        self.service.process_heartbeat("device_001")

        device = self.service.devices["device_001"]
        device.last_heartbeat = datetime.now() - timedelta(minutes=2)
        self.service.check_timeouts()

        self.assertEqual(device.status, "offline")
        self.assertEqual(device.alert_count, 1)

        self.service.process_heartbeat("device_001")

        self.assertEqual(device.status, "online")
        self.assertEqual(device.alert_count, 0)
        self.assertIsNone(device.offline_time)
        self.assertEqual(len(callback_events), 2)
        self.assertEqual(callback_events[1][1], "online")

    def test_monitor_thread(self):
        self.service.start_monitor(check_interval_seconds=0.1)
        self.service.process_heartbeat("device_001")

        device = self.service.devices["device_001"]
        device.last_heartbeat = datetime.now() - timedelta(minutes=2)

        time.sleep(0.3)
        self.assertEqual(device.status, "offline")

        self.service.stop_monitor()

    def test_heartbeat_metadata_update(self):
        self.service.process_heartbeat("device_001", {"version": "1.0"})
        self.service.process_heartbeat("device_001", {"version": "2.0", "status": "running"})

        device = self.service.devices["device_001"]
        self.assertEqual(device.metadata["version"], "2.0")
        self.assertEqual(device.metadata["status"], "running")

    def test_multiple_devices_timeout(self):
        for i in range(5):
            self.service.process_heartbeat(f"device_{i:03d}")

        for i in range(3):
            device = self.service.devices[f"device_{i:03d}"]
            device.last_heartbeat = datetime.now() - timedelta(minutes=2)

        offline = self.service.check_timeouts()
        self.assertEqual(len(offline), 3)

        online_devices = self.service.get_all_devices("online")
        self.assertEqual(len(online_devices), 2)

        offline_devices = self.service.get_all_devices("offline")
        self.assertEqual(len(offline_devices), 3)


class TestFlaskAPI(unittest.TestCase):

    def setUp(self):
        self.config = AlertConfig(timeout_minutes=5)
        self.app = create_app(self.config)
        self.client = self.app.test_client()

    def test_heartbeat_endpoint(self):
        response = self.client.post('/api/heartbeat', json={
            "device_id": "device_001",
            "metadata": {"ip": "10.0.0.1"}
        })
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["code"], 0)
        self.assertEqual(data["data"]["device_id"], "device_001")
        self.assertEqual(data["data"]["status"], "online")

    def test_heartbeat_endpoint_missing_id(self):
        response = self.client.post('/api/heartbeat', json={})
        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertEqual(data["code"], 400)

    def test_list_devices_endpoint(self):
        self.client.post('/api/heartbeat', json={"device_id": "device_001"})
        self.client.post('/api/heartbeat', json={"device_id": "device_002"})

        response = self.client.get('/api/devices')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["code"], 0)
        self.assertEqual(data["count"], 2)

    def test_list_devices_with_filter(self):
        self.client.post('/api/heartbeat', json={"device_id": "device_001"})
        self.client.post('/api/heartbeat', json={"device_id": "device_002"})

        service = self.app.config['HEARTBEAT_SERVICE']
        service.devices["device_002"].status = "offline"

        response = self.client.get('/api/devices?status=offline')
        data = response.get_json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["data"][0]["device_id"], "device_002")

    def test_get_single_device(self):
        self.client.post('/api/heartbeat', json={
            "device_id": "device_001",
            "metadata": {"location": "warehouse"}
        })

        response = self.client.get('/api/devices/device_001')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["data"]["device_id"], "device_001")
        self.assertEqual(data["data"]["metadata"]["location"], "warehouse")

    def test_get_single_device_not_found(self):
        response = self.client.get('/api/devices/nonexistent')
        self.assertEqual(response.status_code, 404)
        data = response.get_json()
        self.assertEqual(data["code"], 404)

    def test_health_endpoint(self):
        response = self.client.get('/api/health')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["code"], 0)
        self.assertIn("timestamp", data["data"])


if __name__ == '__main__':
    unittest.main(verbosity=2)

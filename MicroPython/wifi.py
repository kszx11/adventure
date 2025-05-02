import network
import time
import urequests
import config


def get_external_ip():
    try:
        url = "http://checkip.amazonaws.com/"
        response = urequests.get(url)
        ip = response.text.strip()
        response.close()
        print("External IP:", ip)
        return ip
    except Exception as e:
        print("Error getting external IP:", e)
        return None


def connect_wifi():
    settings = config.read_config_settings()

    WIFI_SSID = settings["ssid"]
    WIFI_PASSWORD = settings["password"]

    print(f"Connecting to {WIFI_SSID}...")

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)

    max_wait = 10
    while max_wait > 0:
        if wlan.isconnected():
            break
        max_wait -= 1
        time.sleep(1)

    if wlan.isconnected():
        print('Connected to', WIFI_SSID)
        # print("Connection data:", wlan.ifconfig())
        return wlan
    else:
        print('Failed to connect to', WIFI_SSID)
        return None

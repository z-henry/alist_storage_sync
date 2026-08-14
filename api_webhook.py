from time import perf_counter

import requests

from config import webhook_url


headers = {"Content-Type": "application/json"}


def media_update_detail(paths):
    payload = {"Updates": [{"Path": path} for path in paths]}
    started = perf_counter()
    try:
        response = requests.post(webhook_url, json=payload, headers=headers)
        return {
            "success": response.status_code in (200, 204),
            "status_code": response.status_code,
            "duration_ms": round((perf_counter() - started) * 1000),
            "response": response.text[:2000] if response.text else None,
            "error": None,
            "payload": payload,
        }
    except requests.RequestException as error:
        return {
            "success": False,
            "status_code": None,
            "duration_ms": round((perf_counter() - started) * 1000),
            "response": None,
            "error": str(error),
            "payload": payload,
        }


def media_update(paths):
    return media_update_detail(paths)["success"]
    return response.status_code == 200 or response.status_code == 204

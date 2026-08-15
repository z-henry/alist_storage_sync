from time import perf_counter

import requests

import config


def media_update_detail(paths):
    payload = {
        "Updates": [{"Path": config.emby_mount_path + path} for path in paths]
    }
    started = perf_counter()
    try:
        response = requests.post(
            f"{config.emby_url}/emby/Library/Media/Updated",
            json=payload,
            headers={
                "X-Emby-Token": config.emby_apikey,
                "Content-Type": "application/json",
            },
            timeout=config.alist_request_timeout_seconds,
        )
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

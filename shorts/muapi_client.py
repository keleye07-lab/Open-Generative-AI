"""Minimal MuAPI client for scripts (stdlib only).

Mirrors the browser client in packages/studio/src/muapi.js:
  POST {base}/api/v1/{endpoint}               -> {request_id}
  GET  {base}/api/v1/predictions/{id}/result  -> {status, outputs: [url]}
  POST {base}/api/v1/upload_file (multipart)  -> {url}
"""
import json, os, time, uuid, urllib.request, urllib.error

BASE_URL = os.environ.get("MUAPI_BASE_URL", "https://api.muapi.ai")
SUCCESS = {"completed", "succeeded", "success"}
FAILURE = {"failed", "error", "cancelled", "canceled"}


class MuapiError(RuntimeError):
    pass


class Muapi:
    def __init__(self, api_key=None, base_url=BASE_URL, log=print):
        self.key = api_key or os.environ.get("MUAPI_KEY") or os.environ.get("MUAPI_API_KEY")
        if not self.key:
            raise MuapiError("No MuAPI key: set MUAPI_KEY (or run with --mock).")
        self.base = base_url.rstrip("/")
        self.log = log

    # ------------------------------------------------------------- http
    def _req(self, method, url, body=None, headers=None, timeout=120, retries=4):
        h = {"x-api-key": self.key, **(headers or {})}
        for attempt in range(retries + 1):
            req = urllib.request.Request(url, data=body, method=method, headers=h)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return json.loads(r.read().decode() or "{}")
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:300]
                if e.code in (401, 403):
                    raise MuapiError(f"MuAPI refused the key ({e.code}): {detail}")
                if e.code == 402:
                    raise MuapiError(f"MuAPI: out of credits (402): {detail}")
                if e.code >= 500 or e.code == 429:
                    if attempt < retries:
                        time.sleep(2 ** attempt * 2); continue
                raise MuapiError(f"MuAPI {method} {url} -> {e.code}: {detail}")
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < retries:
                    time.sleep(2 ** attempt * 2); continue
                raise MuapiError(f"MuAPI network error for {url}: {e}")

    # ------------------------------------------------------------- api
    def submit(self, endpoint, payload):
        data = self._req("POST", f"{self.base}/api/v1/{endpoint}", json.dumps(payload).encode(),
                         {"Content-Type": "application/json"})
        rid = data.get("request_id") or data.get("id")
        if not rid:   # some endpoints answer synchronously
            return None, data
        return rid, data

    def wait(self, request_id, interval=3, timeout_s=1800):
        t0 = time.time()
        while True:
            res = self._req("GET", f"{self.base}/api/v1/predictions/{request_id}/result")
            st = str(res.get("status", "")).lower()
            if st in SUCCESS:
                return res
            if st in FAILURE:
                raise MuapiError(f"Generation {request_id} failed: {res.get('error') or res}")
            if time.time() - t0 > timeout_s:
                raise MuapiError(f"Generation {request_id} timed out after {timeout_s}s")
            time.sleep(interval)

    def run(self, endpoint, payload, timeout_s=1800):
        """Submit + poll; returns the first output URL."""
        self.log(f"  -> muapi {endpoint}")
        rid, data = self.submit(endpoint, payload)
        res = data if rid is None else self.wait(rid, timeout_s=timeout_s)
        url = (res.get("outputs") or [None])[0] or res.get("url") or (res.get("output") or {}).get("url")
        if not url:
            raise MuapiError(f"No output URL from {endpoint}: {res}")
        return url

    def upload(self, path):
        boundary = uuid.uuid4().hex
        name = os.path.basename(path)
        with open(path, "rb") as f:
            content = f.read()
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
                f"Content-Type: application/octet-stream\r\n\r\n").encode() + content + f"\r\n--{boundary}--\r\n".encode()
        data = self._req("POST", f"{self.base}/api/v1/upload_file", body,
                         {"Content-Type": f"multipart/form-data; boundary={boundary}"}, timeout=300)
        url = data.get("url") or data.get("file_url") or (data.get("data") or {}).get("url")
        if not url:
            raise MuapiError(f"Upload returned no URL: {data}")
        return url


def download(url, path):
    req = urllib.request.Request(url, headers={"User-Agent": "shorts-pipeline"})
    with urllib.request.urlopen(req, timeout=300) as r, open(path, "wb") as f:
        while chunk := r.read(1 << 16):
            f.write(chunk)
    return path

"""Shared HTTP session with a polite User-Agent and simple retries."""
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = "LiveMap/1.0 (+https://github.com/masterxhackerx420-oss/wolfsec-live-map)"


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})
    retry = Retry(total=2, backoff_factor=1.0, status_forcelist=(500, 502, 503, 504),
                  allowed_methods=("GET", "POST"))
    s.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=16))
    s.mount("http://", HTTPAdapter(max_retries=retry, pool_maxsize=16))
    return s


SESSION = make_session()


def get(url, timeout=30, **kw):
    r = SESSION.get(url, timeout=timeout, **kw)
    r.raise_for_status()
    return r


def get_json(url, timeout=30, **kw):
    return get(url, timeout=timeout, **kw).json()
